"""Authentication: register, login, logout, session helpers."""
import functools

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for,
)
from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from . import credits
from .db import get_db

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
        db = get_db()
        error = None

        if not email or "@" not in email:
            error = "A valid email is required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."

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
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("main.dashboard"))
    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
