"""Main app routes: dashboard, resume upload, search, results, download."""
import json
import uuid
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect,
    render_template, request, send_file, url_for,
)
from werkzeug.utils import secure_filename

from . import credits, payments
from .auth import login_required
from .db import get_db
from .pipeline.resume import analyze_resume, skills_to_json
from .search_runner import start_search

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    if g.user:
        return redirect(url_for("main.dashboard"))
    return render_template("index.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    resumes = db.execute(
        "SELECT * FROM resumes WHERE user_id = ? ORDER BY created_at DESC",
        (g.user["id"],)).fetchall()
    searches = db.execute(
        "SELECT * FROM searches WHERE user_id = ? ORDER BY created_at DESC LIMIT 25",
        (g.user["id"],)).fetchall()
    google_on = bool(current_app.config.get("SERPAPI_KEY"))
    return render_template("dashboard.html", resumes=resumes, searches=searches,
                           google_on=google_on)


@bp.route("/resume/upload", methods=("POST",))
@login_required
def upload_resume():
    file = request.files.get("resume")
    if not file or not file.filename:
        flash("Choose a resume file to upload.", "error")
        return redirect(url_for("main.dashboard"))
    ext = Path(file.filename).suffix.lower()
    if ext not in current_app.config["ALLOWED_RESUME_EXT"]:
        flash("Unsupported file type. Upload a PDF, DOCX, or TXT.", "error")
        return redirect(url_for("main.dashboard"))

    safe = secure_filename(file.filename)
    stored = f"{uuid.uuid4().hex}{ext}"
    stored_path = str(Path(current_app.config["UPLOAD_DIR"]) / stored)
    file.save(stored_path)

    try:
        text, skills, profile = analyze_resume(stored_path)
    except Exception as e:  # noqa: BLE001
        flash(f"Could not read that resume: {e}", "error")
        return redirect(url_for("main.dashboard"))
    if not text.strip():
        flash("No text could be extracted from that file (is it a scanned image?).",
              "error")
        return redirect(url_for("main.dashboard"))

    db = get_db()
    db.execute(
        "INSERT INTO resumes (user_id, filename, stored_path, text, skills, profile) "
        "VALUES (?,?,?,?,?,?)",
        (g.user["id"], safe, stored_path, text, skills_to_json(skills),
         json.dumps(profile)))
    db.commit()
    n_terms = len(profile.get("terms", []))
    flash(f"Resume uploaded — detected {len(skills)} core skills and {n_terms} "
          f"profile keywords from your skills, education & professional development.",
          "success")
    return redirect(url_for("main.dashboard"))


@bp.route("/search/new", methods=("POST",))
@login_required
def new_search():
    db = get_db()
    resume_id = request.form.get("resume_id") or None
    if resume_id:
        owned = db.execute("SELECT id FROM resumes WHERE id = ? AND user_id = ?",
                           (resume_id, g.user["id"])).fetchone()
        if not owned:
            abort(403)

    min_pay = request.form.get("min_pay", "").replace(",", "").replace("$", "").strip()
    min_pay = int(min_pay) if min_pay.isdigit() else None

    # spend a credit (free first, then paid); block if out
    source = credits.charge_search(db, g.user["id"])
    if source is None:
        flash("You're out of search credits. Grab a credit pack to keep searching — "
              "or your free credit refills weekly.", "error")
        return redirect(url_for("main.credits"))

    cur = db.execute(
        "INSERT INTO searches (user_id, resume_id, title_query, location, min_pay, "
        "work_type, languages, credit_source, status) VALUES (?,?,?,?,?,?,?,?, 'pending')",
        (g.user["id"], resume_id,
         (request.form.get("title_query") or "").strip(),
         (request.form.get("location") or "").strip(),
         min_pay,
         request.form.get("work_type") or "any",
         (request.form.get("languages") or "").strip(),
         source))
    db.commit()
    search_id = cur.lastrowid

    start_search(current_app._get_current_object(), search_id)
    return redirect(url_for("main.view_search", search_id=search_id))


@bp.route("/credits", endpoint="credits")
@login_required
def credits_page():
    db = get_db()
    bal = credits.balance(db, g.user["id"])
    history = db.execute(
        "SELECT * FROM credit_ledger WHERE user_id = ? ORDER BY id DESC LIMIT 25",
        (g.user["id"],)).fetchall()
    return render_template("credits.html", bal=bal, packs=payments.list_packs(),
                           history=history, live=payments.is_live(current_app))


@bp.route("/credits/buy/<pack_id>", methods=("POST",))
@login_required
def buy_credits(pack_id):
    pack = payments.get_pack(pack_id)
    if not pack:
        abort(404)
    success = url_for("main.credits", _external=True)
    cancel = success
    outcome = payments.create_checkout(
        current_app._get_current_object(), pack, g.user, success, cancel)
    if outcome["mode"] == "redirect":
        return redirect(outcome["url"])
    # stub / test mode — fulfill instantly
    credits.add_purchase(get_db(), g.user["id"], pack["credits"],
                         note=f"{pack['name']} pack (test mode)")
    flash(f"(Test mode) Added {pack['credits']} credits. Connect Stripe to charge "
          "real payments.", "success")
    return redirect(url_for("main.credits"))


@bp.route("/credits/webhook", methods=("POST",))
def stripe_webhook():
    """Stripe calls this after a successful payment; we add the credits here.

    Verifies the signature and is idempotent (each event fulfilled at most once).
    """
    import sqlite3
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        abort(404)
    import stripe
    try:
        event = stripe.Webhook.construct_event(
            request.get_data(), request.headers.get("Stripe-Signature", ""), secret)
    except Exception:  # noqa: BLE001 — bad signature / malformed
        abort(400)

    if event.get("type") == "checkout.session.completed":
        db = get_db()
        eid = event.get("id")
        # idempotency: skip if we've already processed this event
        if eid and db.execute("SELECT 1 FROM stripe_events WHERE id = ?",
                              (eid,)).fetchone():
            return "", 200

        def _ids(obj):
            meta = obj.get("metadata") or {}
            try:
                return (int(meta.get("user_id") or obj.get("client_reference_id") or 0),
                        int(meta.get("credits") or 0))
            except (TypeError, ValueError):
                return 0, 0

        try:
            sess = (event.get("data") or {}).get("object") or {}
            uid, n = _ids(sess)
            # Thin-payload destinations omit metadata — fetch the full session by id.
            if not uid or not n:
                sess_id = sess.get("id") or (event.get("related_object") or {}).get("id")
                if sess_id and str(sess_id).startswith("cs_"):
                    stripe.api_key = current_app.config.get("STRIPE_SECRET_KEY")
                    full = stripe.checkout.Session.retrieve(sess_id)
                    uid, n = _ids(full)
                    sess = full

            note = f"Stripe purchase {sess.get('id', '')}"
            if uid and n:
                exists = db.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone()
                if exists:
                    credits.add_purchase(db, uid, n, note=note)
                else:
                    current_app.logger.warning(
                        "Stripe webhook: user %s no longer exists; cannot credit %s",
                        uid, note)
            else:
                current_app.logger.warning(
                    "Stripe webhook: no user_id/credits on event %s", eid)
            # mark processed only after we've handled it (no further retries needed)
            db.execute("INSERT OR IGNORE INTO stripe_events (id) VALUES (?)", (eid,))
            db.commit()
        except Exception:  # noqa: BLE001 — log + 500 so Stripe retries transient errors
            current_app.logger.exception("Stripe webhook fulfillment failed")
            return "", 500
    return "", 200


def _owned_search(search_id):
    s = get_db().execute(
        "SELECT * FROM searches WHERE id = ? AND user_id = ?",
        (search_id, g.user["id"])).fetchone()
    if s is None:
        abort(404)
    return s


@bp.route("/search/<int:search_id>")
@login_required
def view_search(search_id):
    s = _owned_search(search_id)
    jobs = get_db().execute(
        "SELECT * FROM jobs WHERE search_id = ? ORDER BY match_score DESC",
        (search_id,)).fetchall()
    return render_template("search.html", search=s, jobs=jobs)


@bp.route("/search/<int:search_id>/status")
@login_required
def search_status(search_id):
    s = _owned_search(search_id)
    return jsonify({
        "status": s["status"],
        "progress": s["progress"],
        "result_count": s["result_count"],
        "error": s["error"],
        "has_export": bool(s["export_path"]),
    })


@bp.route("/search/<int:search_id>/download")
@login_required
def download_search(search_id):
    s = _owned_search(search_id)
    if not s["export_path"] or not Path(s["export_path"]).exists():
        flash("Export not ready yet.", "error")
        return redirect(url_for("main.view_search", search_id=search_id))
    return send_file(s["export_path"], as_attachment=True,
                     download_name=Path(s["export_path"]).name)
