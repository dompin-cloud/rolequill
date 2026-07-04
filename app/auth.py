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

RESET_TTL = timedelta(hours=1)      # password-reset links expire after this
CODE_TTL = timedelta(minutes=10)    # emailed 2FA codes expire after this
CODE_MAX_ATTEMPTS = 5               # wrong guesses before a code is burned
TRUSTED_TTL = timedelta(days=30)    # "remember this device" lifetime
TRUSTED_COOKIE = "rq_td"


def _hash_token(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def _gen_code():
    """A 6-digit numeric code, uniformly random (leading zeros kept)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _issue_code(db, user, purpose):
    """Create a fresh single-use code for `purpose` and email it. Any earlier
    unused code for the same purpose is dropped so only one is ever live."""
    code = _gen_code()
    expires = (datetime.now(timezone.utc) + CODE_TTL).isoformat()
    db.execute("DELETE FROM login_codes WHERE user_id = ? AND purpose = ? AND used_at IS NULL",
               (user["id"], purpose))
    db.execute("INSERT INTO login_codes (user_id, code_hash, purpose, expires_at) "
               "VALUES (?,?,?,?)", (user["id"], _hash_token(code), purpose, expires))
    db.commit()
    html = render_template("email/login_code.html", code=code,
                           name=user["full_name"], purpose=purpose)
    mailer.send_email(user["email"], "Your RoleQuill verification code", html,
                      text=f"Your RoleQuill verification code is {code}.\n"
                           "It expires in 10 minutes. If you didn't request it, "
                           "you can ignore this email.")


def _verify_code(db, user_id, purpose, code):
    """Check a submitted code. Returns (ok, error_message). Counts wrong guesses
    and burns the code on success (single-use)."""
    row = db.execute(
        "SELECT * FROM login_codes WHERE user_id = ? AND purpose = ? AND used_at IS NULL "
        "ORDER BY id DESC LIMIT 1", (user_id, purpose)).fetchone()
    if row is None:
        return False, "No active code — request a new one."
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        return False, "That code has expired — request a new one."
    if row["attempts"] >= CODE_MAX_ATTEMPTS:
        return False, "Too many incorrect attempts — request a new code."
    if _hash_token((code or "").strip()) != row["code_hash"]:
        db.execute("UPDATE login_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        db.commit()
        return False, "Incorrect code."
    db.execute("UPDATE login_codes SET used_at = datetime('now') WHERE id = ?", (row["id"],))
    db.commit()
    return True, None


def _device_trusted(db, user_id):
    """True if this browser presents a valid, unexpired 'remember me' token."""
    tok = request.cookies.get(TRUSTED_COOKIE)
    if not tok:
        return False
    row = db.execute("SELECT expires_at FROM trusted_devices "
                     "WHERE token_hash = ? AND user_id = ?",
                     (_hash_token(tok), user_id)).fetchone()
    return bool(row and datetime.fromisoformat(row["expires_at"])
                > datetime.now(timezone.utc))


def _remember_device(resp, db, user_id):
    """Issue a trusted-device token, store its hash, and set the cookie on `resp`."""
    tok = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + TRUSTED_TTL
    db.execute("INSERT INTO trusted_devices (user_id, token_hash, expires_at) VALUES (?,?,?)",
               (user_id, _hash_token(tok), expires.isoformat()))
    db.commit()
    resp.set_cookie(TRUSTED_COOKIE, tok, max_age=int(TRUSTED_TTL.total_seconds()),
                    httponly=True, samesite="Lax",
                    secure=current_app.config.get("SESSION_COOKIE_SECURE", False))

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
            # Second factor: if the account has email 2FA on and this browser isn't a
            # remembered device, hold the login as "pending" and challenge for a code.
            if user["twofa_email"] and not _device_trusted(db, user["id"]):
                _issue_code(db, user, "login")
                session.clear()
                session["pending_2fa_user"] = user["id"]
                return redirect(url_for("auth.verify"))
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("main.dashboard"))
    return render_template("auth/login.html")


@bp.route("/verify", methods=("GET", "POST"))
def verify():
    """Second-factor challenge: enter the emailed code to finish logging in."""
    uid = session.get("pending_2fa_user")
    if not uid:
        return redirect(url_for("auth.login"))
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user is None:
        session.pop("pending_2fa_user", None)
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        key = f"2fa:{ip}:{uid}"
        ok_rl, retry = ratelimit.check(key, ratelimit.MAX_FAILS)
        if not ok_rl:
            flash(f"Too many attempts. Try again in about "
                  f"{max(1, retry // 60)} minute(s).", "error")
            return render_template("auth/verify.html")
        ok, err = _verify_code(db, uid, "login", request.form.get("code"))
        if not ok:
            ratelimit.record_failure(key)
            flash(err, "error")
            return render_template("auth/verify.html")
        ratelimit.clear(key)
        session.clear()
        session["user_id"] = uid
        resp = redirect(url_for("main.dashboard"))
        if request.form.get("remember"):
            _remember_device(resp, db, uid)
        return resp
    return render_template("auth/verify.html")


@bp.route("/verify/resend", methods=("POST",))
def verify_resend():
    uid = session.get("pending_2fa_user")
    if not uid:
        return redirect(url_for("auth.login"))
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user:
        ip = request.remote_addr or "unknown"
        key = f"2fa-resend:{ip}:{uid}"
        ok, _ = ratelimit.check(key, 5)       # cap resends per IP+account
        if ok:
            ratelimit.record_failure(key)
            _issue_code(db, user, "login")
            flash("A new code is on its way — check your inbox.", "success")
        else:
            flash("Please wait a bit before requesting another code.", "error")
    return redirect(url_for("auth.verify"))


@bp.route("/2fa/enable", methods=("POST",))
@login_required
def twofa_enable():
    """Step 1 of turning 2FA on: email a code to confirm the address works."""
    db = get_db()
    if g.user["twofa_email"]:
        flash("Two-factor authentication is already on.", "info")
        return redirect(url_for("main.account"))
    if not mailer.is_configured():
        flash("Email isn't configured, so email-based 2FA can't be enabled right now.",
              "error")
        return redirect(url_for("main.account"))
    _issue_code(db, g.user, "enroll")
    flash("We emailed you a 6-digit code — enter it below to finish enabling 2FA.",
          "success")
    return redirect(url_for("auth.twofa_confirm"))


@bp.route("/2fa/confirm", methods=("GET", "POST"))
@login_required
def twofa_confirm():
    """Step 2: verify the enrollment code, then flip the flag on."""
    db = get_db()
    if g.user["twofa_email"]:
        return redirect(url_for("main.account"))
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        key = f"2fa-enroll:{ip}:{g.user['id']}"
        ok_rl, retry = ratelimit.check(key, ratelimit.MAX_FAILS)
        if not ok_rl:
            flash(f"Too many attempts. Try again in about "
                  f"{max(1, retry // 60)} minute(s).", "error")
            return render_template("auth/twofa_confirm.html")
        ok, err = _verify_code(db, g.user["id"], "enroll", request.form.get("code"))
        if not ok:
            ratelimit.record_failure(key)
            flash(err, "error")
            return render_template("auth/twofa_confirm.html")
        ratelimit.clear(key)
        db.execute("UPDATE users SET twofa_email = 1 WHERE id = ?", (g.user["id"],))
        db.commit()
        flash("Two-factor authentication is on. You'll enter an emailed code when "
              "you log in on a new device.", "success")
        return redirect(url_for("main.account"))
    return render_template("auth/twofa_confirm.html")


@bp.route("/2fa/disable", methods=("POST",))
@login_required
def twofa_disable():
    """Turn 2FA off. Requires the current password so a hijacked session can't."""
    db = get_db()
    if not check_password_hash(g.user["password_hash"], request.form.get("password") or ""):
        flash("Incorrect password — two-factor authentication was not changed.", "error")
        return redirect(url_for("main.account"))
    db.execute("UPDATE users SET twofa_email = 0 WHERE id = ?", (g.user["id"],))
    db.execute("DELETE FROM trusted_devices WHERE user_id = ?", (g.user["id"],))
    db.execute("DELETE FROM login_codes WHERE user_id = ?", (g.user["id"],))
    db.commit()
    flash("Two-factor authentication has been turned off.", "success")
    return redirect(url_for("main.account"))


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
        # a password reset revokes remembered devices and pending 2FA codes, so a
        # new password always requires a fresh second factor
        db.execute("DELETE FROM trusted_devices WHERE user_id = ?", (row["user_id"],))
        db.execute("DELETE FROM login_codes WHERE user_id = ?", (row["user_id"],))
        db.commit()
        flash("Your password has been reset — please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset.html", token=token)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
