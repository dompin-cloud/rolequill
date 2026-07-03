"""Main app routes: dashboard, resume upload, search, results, download."""
import json
import uuid
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect,
    render_template, request, send_file, url_for,
)
from werkzeug.utils import secure_filename

import functools

from . import credits, payments
from .auth import login_required
from .db import get_db
from .pipeline.resume import analyze_resume, skills_to_json
from .search_runner import start_search

bp = Blueprint("main", __name__)

# application pipeline stages; anything past 'applied' means the company responded
APP_STATUSES = ["saved", "applied", "replied", "interview", "offer", "rejected"]
REPLY_STATUSES = {"replied", "interview", "offer", "rejected"}
SENT_STATUSES = {"applied", "replied", "interview", "offer", "rejected"}


def _application_stats(apps):
    sent = sum(1 for a in apps if a["status"] in SENT_STATUSES)
    replies = sum(1 for a in apps if a["status"] in REPLY_STATUSES)
    return {
        "total": len(apps),
        "sent": sent,
        "replies": replies,
        "reply_rate": round(replies / sent * 100) if sent else 0,
        "interview": sum(1 for a in apps if a["status"] == "interview"),
        "offer": sum(1 for a in apps if a["status"] == "offer"),
    }


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
    apps = db.execute("SELECT status FROM applications WHERE user_id = ?",
                      (g.user["id"],)).fetchall()
    return render_template("dashboard.html", resumes=resumes, searches=searches,
                           google_on=google_on, app_stats=_application_stats(apps))


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
    import json
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        abort(404)
    import stripe
    payload = request.get_data()
    try:
        # verify the signature only; we read fields from the raw JSON below because
        # newer Stripe objects are not dict-like (no .get()).
        stripe.Webhook.construct_event(
            payload, request.headers.get("Stripe-Signature", ""), secret)
    except Exception:  # noqa: BLE001 — bad signature / malformed
        abort(400)

    try:
        event = json.loads(payload)
    except (ValueError, TypeError):
        return "", 200

    if event.get("type") != "checkout.session.completed":
        return "", 200

    db = get_db()
    eid = event.get("id")
    if eid and db.execute("SELECT 1 FROM stripe_events WHERE id = ?",
                          (eid,)).fetchone():
        return "", 200  # already processed

    def _ids(obj):
        obj = obj or {}
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
                try:
                    stripe.api_key = current_app.config.get("STRIPE_SECRET_KEY")
                    full = json.loads(str(stripe.checkout.Session.retrieve(sess_id)))
                    uid, n = _ids(full)
                    sess = full
                except Exception:  # noqa: BLE001
                    pass

        note = f"Stripe purchase {sess.get('id', '')}"
        if uid and n:
            if db.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone():
                credits.add_purchase(db, uid, n, note=note)
            else:
                current_app.logger.warning(
                    "Stripe webhook: user %s no longer exists; cannot credit %s",
                    uid, note)
        else:
            current_app.logger.warning(
                "Stripe webhook: no user_id/credits on event %s", eid)
        db.execute("INSERT OR IGNORE INTO stripe_events (id) VALUES (?)", (eid,))
        db.commit()
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Stripe webhook fulfillment failed")
        return "", 500
    return "", 200


def admin_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        # 404 (not 403) so the page's existence isn't revealed to non-admins
        if not getattr(g, "is_admin", False):
            abort(404)
        return view(**kwargs)
    return wrapped


@bp.route("/admin")
@login_required
@admin_required
def admin():
    db = get_db()

    def scalar(q, *a):
        return db.execute(q, a).fetchone()[0]

    app_rows = db.execute("SELECT status FROM applications").fetchall()
    searches_done = scalar("SELECT COUNT(*) FROM searches WHERE status='done'")
    total_matches = scalar("SELECT COALESCE(SUM(result_count),0) FROM searches "
                           "WHERE status='done'")

    m = {
        "users": scalar("SELECT COUNT(*) FROM users"),
        "users_7d": scalar("SELECT COUNT(*) FROM users "
                           "WHERE created_at >= datetime('now','-7 days')"),
        "applications": len(app_rows),
        "reply_rate": _application_stats(app_rows)["reply_rate"],
        "searches_done": searches_done,
        "total_matches": total_matches,
        "avg_matches": round(total_matches / searches_done, 1) if searches_done else 0,
    }
    recent_users = db.execute(
        "SELECT u.email, u.created_at, "
        "(SELECT COUNT(*) FROM searches s WHERE s.user_id=u.id) AS searches, "
        "(SELECT COUNT(*) FROM applications a WHERE a.user_id=u.id) AS apps "
        "FROM users u ORDER BY u.id DESC LIMIT 15").fetchall()
    recent_searches = db.execute(
        "SELECT s.title_query, s.status, s.result_count, s.created_at, u.email "
        "FROM searches s JOIN users u ON s.user_id = u.id "
        "ORDER BY s.id DESC LIMIT 12").fetchall()
    return render_template("admin.html", m=m, recent_users=recent_users,
                           recent_searches=recent_searches)


@bp.route("/applications")
@login_required
def applications():
    db = get_db()
    apps = db.execute(
        "SELECT * FROM applications WHERE user_id = ? "
        "ORDER BY COALESCE(applied_at, created_at) DESC", (g.user["id"],)).fetchall()
    return render_template("applications.html", apps=apps,
                           stats=_application_stats(apps), statuses=APP_STATUSES)


@bp.route("/applications/add", methods=("POST",))
@login_required
def add_application():
    db = get_db()
    job = db.execute(
        "SELECT j.* FROM jobs j JOIN searches s ON j.search_id = s.id "
        "WHERE j.id = ? AND s.user_id = ?",
        (request.form.get("job_id"), g.user["id"])).fetchone()
    if not job:
        abort(404)
    cur = db.execute(
        "INSERT OR IGNORE INTO applications (user_id, role, company, apply_link, "
        "source, match_score, salary, location, status, applied_at) "
        "VALUES (?,?,?,?,?,?,?,?, 'applied', datetime('now'))",
        (g.user["id"], job["role"], job["company"], job["apply_link"], job["source"],
         job["match_score"], job["salary"], job["location"]))
    db.commit()
    flash("Marked as applied — track replies on your Applications page."
          if cur.rowcount else "Already in your applications.", "success")
    return redirect(request.referrer or url_for("main.applications"))


@bp.route("/applications/<int:app_id>/status", methods=("POST",))
@login_required
def update_application(app_id):
    db = get_db()
    app = db.execute("SELECT * FROM applications WHERE id = ? AND user_id = ?",
                     (app_id, g.user["id"])).fetchone()
    if not app:
        abort(404)
    new = request.form.get("status")
    if new not in APP_STATUSES:
        abort(400)
    # stamp applied_at once it's sent, replied_at the first time a reply lands
    set_replied = new in REPLY_STATUSES and not app["replied_at"]
    db.execute(
        "UPDATE applications SET status = ?, "
        "applied_at = COALESCE(applied_at, CASE WHEN ? != 'saved' THEN datetime('now') END), "
        "replied_at = CASE WHEN ? THEN datetime('now') ELSE replied_at END "
        "WHERE id = ?",
        (new, new, 1 if set_replied else 0, app_id))
    db.commit()
    return redirect(url_for("main.applications"))


@bp.route("/applications/<int:app_id>/delete", methods=("POST",))
@login_required
def delete_application(app_id):
    db = get_db()
    db.execute("DELETE FROM applications WHERE id = ? AND user_id = ?",
               (app_id, g.user["id"]))
    db.commit()
    return redirect(url_for("main.applications"))


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


@bp.route("/search/<int:search_id>/report")
@login_required
def search_report(search_id):
    s = _owned_search(search_id)
    if not s["report_json"]:
        flash("The full report isn't ready for this search.", "error")
        return redirect(url_for("main.view_search", search_id=search_id))
    jobs = get_db().execute(
        "SELECT * FROM jobs WHERE search_id = ? ORDER BY match_score DESC",
        (search_id,)).fetchall()
    report = json.loads(s["report_json"])
    return render_template("report.html", search=s, report=report, jobs=jobs)


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
