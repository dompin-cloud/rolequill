"""Authentication: register, login, logout, password reset, session helpers."""
import functools
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for,
)
from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from . import credits, mailer, ratelimit
from .db import get_db

RESET_TTL = timedelta(hours=1)   # password-reset links expire after this


def _hash_token(tok):
    return hashlib.sha256(tok.encode()).hexdigest()

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
        g.credits = None
        g.is_admin = False
        return
    db = get_db()
    credits.ensure_weekly_refill(db, user_id)
    g.user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    g.credits = credits.balance(db, user_id)["total"] if g.user else None
    admin_email = current_app.config.get("ADMIN_EMAIL")
    g.is_admin = bool(g.user and admin_email
                      and g.user["email"].strip().lower() == admin_email)


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(**kwargs)
    return wrapped


@bp.route("/register", methods=("GET", "POST"))
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        full_name = (request.form.get("full_name") or "").strip()
        consent = request.form.get("consent")
        db = get_db()
        error = None

        if not email or "@" not in email:
            error = "A valid email is required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif not consent:
            error = "Please agree to the Terms of Service and Privacy Policy to continue."

        if error is None:
            try:
                db.execute(
                    "INSERT INTO users (email, password_hash, full_name) VALUES (?, ?, ?)",
                    (email, generate_password_hash(password), full_name),
                )
                db.commit()
            except db.IntegrityError:
                error = f"An account for {email} already exists."
            else:
                user = db.execute(
                    "SELECT * FROM users WHERE email = ?", (email,)
                ).fetchone()
                credits.grant_signup(db, user["id"])
                session.clear()
                session["user_id"] = user["id"]
                flash(f"Welcome! You have {credits.SIGNUP_BONUS} free search "
                      "credits to start.", "success")
                return redirect(url_for("main.dashboard"))
        flash(error, "error")
    return render_template("auth/register.html")


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        ip = request.remote_addr or "unknown"
        key_user = f"login:{ip}:{email}"       # targeted attack on one account
        key_ip = f"login-ip:{ip}"              # spray across many accounts from one host

        ok_user, retry_u = ratelimit.check(key_user, ratelimit.MAX_FAILS)
        ok_ip, retry_i = ratelimit.check(key_ip, ratelimit.MAX_IP_FAILS)
        if not (ok_user and ok_ip):
            mins = max(1, max(retry_u, retry_i) // 60)
            flash(f"Too many failed attempts. Try again in about {mins} minute(s).",
                  "error")
            return render_template("auth/login.html")

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            # count the failure against both the account and the source IP
            ratelimit.record_failure(key_user)
            ratelimit.record_failure(key_ip)
            flash("Incorrect email or password.", "error")
        else:
            ratelimit.clear(key_user)          # good login clears this account's counter
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("main.dashboard"))
    return render_template("auth/login.html")


@bp.route("/forgot", methods=("GET", "POST"))
def forgot():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        ip = request.remote_addr or "unknown"
        key = f"forgot:{ip}"
        ok, retry = ratelimit.check(key, 5)   # cap reset requests per IP
        if not ok:
            flash(f"Too many requests. Try again in about "
                  f"{max(1, retry // 60)} minute(s).", "error")
            return render_template("auth/forgot.html")
        ratelimit.record_failure(key)          # every request counts toward the cap

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expires = (datetime.now(timezone.utc) + RESET_TTL).isoformat()
            # invalidate any earlier unused tokens, then issue a fresh one
            db.execute("DELETE FROM password_resets WHERE user_id = ? AND used_at IS NULL",
                       (user["id"],))
            db.execute("INSERT INTO password_resets (user_id, token_hash, expires_at) "
                       "VALUES (?,?,?)", (user["id"], _hash_token(token), expires))
            db.commit()
            link = url_for("auth.reset", token=token, _external=True)
            html = render_template("email/reset.html", link=link,
                                   name=user["full_name"])
            mailer.send_email(email, "Reset your RoleQuill password", html,
                              text=f"Reset your RoleQuill password:\n{link}\n\n"
                                   "This link expires in 1 hour. If you didn't request "
                                   "it, you can ignore this email.")
        # identical response whether or not the account exists (no enumeration)
        flash("If an account exists for that email, we've sent a reset link. "
              "Check your inbox (and spam).", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html", email_on=mailer.is_configured())


@bp.route("/reset/<token>", methods=("GET", "POST"))
def reset(token):
    if g.user:
        return redirect(url_for("main.dashboard"))
    db = get_db()
    row = db.execute("SELECT * FROM password_resets WHERE token_hash = ?",
                     (_hash_token(token),)).fetchone()
    valid = bool(row and row["used_at"] is None
                 and datetime.fromisoformat(row["expires_at"])
                 > datetime.now(timezone.utc))
    if not valid:
        flash("That reset link is invalid or has expired. Request a new one.", "error")
        return redirect(url_for("auth.forgot"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/reset.html", token=token)
        if password != confirm:
            flash("Those passwords don't match.", "error")
            return render_template("auth/reset.html", token=token)
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(password), row["user_id"]))
        # burn this token and any other outstanding ones for the account
        db.execute("UPDATE password_resets SET used_at = datetime('now') WHERE id = ?",
                   (row["id"],))
        db.execute("DELETE FROM password_resets WHERE user_id = ? AND used_at IS NULL",
                   (row["user_id"],))
        db.commit()
        flash("Your password has been reset — please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset.html", token=token)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
