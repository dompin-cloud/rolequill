"""Main app routes: dashboard, resume upload, search, results, download."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Blueprint, Response, abort, current_app, flash, g, jsonify, redirect,
    render_template, request, send_file, session, url_for,
)
from werkzeug.utils import secure_filename

import functools

from . import credits, mailer, payments
from .auth import login_required
from .db import get_db
from .pipeline.resume import analyze_resume, skills_to_json
from .search_runner import start_search

bp = Blueprint("main", __name__)

# application pipeline stages; anything past 'applied' means the company responded
APP_STATUSES = ["saved", "applied", "replied", "interview", "offer", "rejected"]
REPLY_STATUSES = {"replied", "interview", "offer", "rejected"}
SENT_STATUSES = {"applied", "replied", "interview", "offer", "rejected"}


def _initials(name, uid):
    """Non-PII display label: initials from the name, else '#<id>'."""
    parts = [p for p in (name or "").split() if p]
    ini = "".join(p[0].upper() for p in parts[:3])
    return ini or f"#{uid}"


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
    jsearch_on = bool(current_app.config.get("JSEARCH_KEY"))
    apps = db.execute("SELECT status FROM applications WHERE user_id = ?",
                      (g.user["id"],)).fetchall()
    return render_template("dashboard.html", resumes=resumes, searches=searches,
                           google_on=google_on, jsearch_on=jsearch_on,
                           app_stats=_application_stats(apps))


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


def _redirect_back(fallback):
    """Redirect to the page the user came from, but only if it's on this host —
    stops an attacker-crafted Referer from bouncing the user off-site."""
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return redirect(ref)
    return redirect(fallback)


def _safe_unlink(path):
    """Best-effort delete of a file on disk; never raises."""
    try:
        if path:
            Path(path).unlink(missing_ok=True)
    except OSError:
        current_app.logger.warning("Could not remove file %s", path)


@bp.route("/resume/<int:resume_id>/delete", methods=("POST",))
@login_required
def delete_resume(resume_id):
    db = get_db()
    r = db.execute("SELECT * FROM resumes WHERE id = ? AND user_id = ?",
                   (resume_id, g.user["id"])).fetchone()
    if not r:
        abort(404)
    # remove the stored file from disk, then the row (searches keep working:
    # searches.resume_id is ON DELETE SET NULL)
    _safe_unlink(r["stored_path"])
    db.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
    db.commit()
    flash(f"Deleted resume “{r['filename']}” and its stored file.", "success")
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

    # Create the search row BEFORE charging so a spent credit is always tied to a
    # row that startup orphan-reconcile can refund if the run never completes.
    # credit_source is filled in right after the charge succeeds below.
    cur = db.execute(
        "INSERT INTO searches (user_id, resume_id, title_query, location, min_pay, "
        "work_type, languages, status) VALUES (?,?,?,?,?,?,?, 'pending')",
        (g.user["id"], resume_id,
         (request.form.get("title_query") or "").strip(),
         (request.form.get("location") or "").strip(),
         min_pay,
         request.form.get("work_type") or "any",
         (request.form.get("languages") or "").strip()))
    search_id = cur.lastrowid
    db.commit()

    # spend a credit (free first, then paid); block (and drop the row) if out.
    # The owner/admin account runs unlimited searches (feature testing) — never charged,
    # never refunded (credit_source 'admin' is excluded from all refund paths).
    if g.is_admin:
        source = "admin"
    else:
        source = credits.charge_search(db, g.user["id"])
        if source is None:
            db.execute("DELETE FROM searches WHERE id = ?", (search_id,))
            db.commit()
            flash("You're out of search credits. Grab a credit pack to keep searching — "
                  "or your free credit refills weekly.", "error")
            return redirect(url_for("main.credits"))
    db.execute("UPDATE searches SET credit_source = ? WHERE id = ?", (source, search_id))
    db.commit()

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
    users_raw = db.execute(
        "SELECT u.id, u.full_name, u.created_at, "
        "(SELECT COUNT(*) FROM searches s WHERE s.user_id=u.id) AS searches, "
        "(SELECT COUNT(*) FROM applications a WHERE a.user_id=u.id) AS apps "
        "FROM users u ORDER BY u.id DESC LIMIT 15").fetchall()
    recent_users = [{"who": _initials(u["full_name"], u["id"]),
                     "created_at": u["created_at"], "searches": u["searches"],
                     "apps": u["apps"]} for u in users_raw]
    searches_raw = db.execute(
        "SELECT s.title_query, s.status, s.result_count, s.created_at, "
        "u.full_name, u.id AS uid "
        "FROM searches s JOIN users u ON s.user_id = u.id "
        "ORDER BY s.id DESC LIMIT 12").fetchall()
    recent_searches = [{"title_query": s["title_query"], "status": s["status"],
                        "result_count": s["result_count"], "created_at": s["created_at"],
                        "who": _initials(s["full_name"], s["uid"])} for s in searches_raw]
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
    return _redirect_back(url_for("main.applications"))


@bp.route("/applications/add-manual", methods=("POST",))
@login_required
def add_application_manual():
    """Add an application by hand (e.g. one applied to before tracking, or off-platform)."""
    db = get_db()
    role = (request.form.get("role") or "").strip()
    company = (request.form.get("company") or "").strip()
    if not role or not company:
        flash("Role and company are required to add an application.", "error")
        return redirect(url_for("main.applications"))

    status = request.form.get("status")
    if status not in APP_STATUSES:
        status = "applied"
    # empty apply_link must be NULL, not '', or the UNIQUE(user_id, apply_link) index
    # would block a second linkless manual entry
    apply_link = (request.form.get("apply_link") or "").strip() or None
    source = (request.form.get("source") or "").strip() or "Manual"
    location = (request.form.get("location") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None

    # applied date: honor a date they typed (lets them backdate older applications),
    # else stamp now for any sent status; 'saved' stays undated
    typed = (request.form.get("applied_at") or "").strip()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    applied_at = typed or (None if status == "saved" else now)
    replied_at = (typed or now) if status in REPLY_STATUSES else None

    cur = db.execute(
        "INSERT OR IGNORE INTO applications (user_id, role, company, apply_link, "
        "source, location, status, applied_at, replied_at, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (g.user["id"], role, company, apply_link, source, location, status,
         applied_at, replied_at, notes))
    db.commit()
    flash(f"Added {role} at {company} to your applications." if cur.rowcount
          else "You're already tracking an application with that apply link.",
          "success" if cur.rowcount else "error")
    return redirect(url_for("main.applications"))


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


# ---------------------------------------------------------------------------
# Account, data export/deletion, and legal pages (privacy self-service)
# ---------------------------------------------------------------------------

@bp.route("/privacy")
def privacy():
    return render_template("legal/privacy.html")


@bp.route("/terms")
def terms():
    return render_template("legal/terms.html")


@bp.route("/account")
@login_required
def account():
    db = get_db()
    resume_count = db.execute(
        "SELECT COUNT(*) FROM resumes WHERE user_id = ?", (g.user["id"],)).fetchone()[0]
    search_count = db.execute(
        "SELECT COUNT(*) FROM searches WHERE user_id = ?", (g.user["id"],)).fetchone()[0]
    app_count = db.execute(
        "SELECT COUNT(*) FROM applications WHERE user_id = ?", (g.user["id"],)).fetchone()[0]
    return render_template("account.html", resume_count=resume_count,
                           search_count=search_count, app_count=app_count,
                           email_on=mailer.is_configured())


@bp.route("/account/export")
@login_required
def export_data():
    """Download everything we hold on this user as a JSON file (right of access)."""
    db = get_db()
    uid = g.user["id"]

    def rows(q):
        return [dict(r) for r in db.execute(q, (uid,)).fetchall()]

    user = dict(db.execute(
        "SELECT id, email, full_name, free_credits, paid_credits, created_at "
        "FROM users WHERE id = ?", (uid,)).fetchone())  # note: no password_hash
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": user,
        "resumes": rows("SELECT id, filename, text, skills, profile, created_at "
                        "FROM resumes WHERE user_id = ?"),
        "searches": rows("SELECT id, title_query, location, min_pay, work_type, "
                         "languages, status, result_count, created_at, finished_at "
                         "FROM searches WHERE user_id = ?"),
        "applications": rows("SELECT id, role, company, apply_link, status, "
                             "applied_at, replied_at, notes, created_at "
                             "FROM applications WHERE user_id = ?"),
        "credit_ledger": rows("SELECT delta, kind, balance_after, note, created_at "
                              "FROM credit_ledger WHERE user_id = ?"),
    }
    body = json.dumps(payload, indent=2, default=str)
    return Response(body, mimetype="application/json", headers={
        "Content-Disposition": f'attachment; filename="rolequill-data-{uid}.json"'})


@bp.route("/account/delete", methods=("POST",))
@login_required
def delete_account():
    """Permanently erase the account and all associated data (right of erasure).

    Requires the user to type their email to confirm. Removes DB rows (children
    cascade via ON DELETE CASCADE) and every resume/export file from disk.
    """
    db = get_db()
    uid = g.user["id"]
    typed = (request.form.get("confirm_email") or "").strip().lower()
    if typed != (g.user["email"] or "").strip().lower():
        flash("Type your account email exactly to confirm deletion.", "error")
        return redirect(url_for("main.account"))

    # collect on-disk files BEFORE deleting the rows
    files = [r["stored_path"] for r in db.execute(
        "SELECT stored_path FROM resumes WHERE user_id = ?", (uid,)).fetchall()]
    files += [r["export_path"] for r in db.execute(
        "SELECT export_path FROM searches WHERE user_id = ? AND export_path IS NOT NULL",
        (uid,)).fetchall()]

    db.execute("DELETE FROM users WHERE id = ?", (uid,))  # cascades to all children
    db.commit()
    for f in files:
        _safe_unlink(f)

    session.clear()
    flash("Your account and all associated data have been permanently deleted.",
          "success")
    return redirect(url_for("main.index"))


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
