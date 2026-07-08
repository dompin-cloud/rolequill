"""Runs a search in a background thread and persists results + Excel export."""
import json
import threading
from datetime import datetime
from pathlib import Path

from . import credits
from .db import standalone_connection
from .pipeline import run_search
from .pipeline.excel_export import build_workbook, post_status
from .pipeline.profile import detect_roles, search_query
from .pipeline.report import build_report
from .providers.base import SearchTimeout


def start_search(app, search_id):
    """Launch the search in a daemon thread using app config (not request scope)."""
    t = threading.Thread(target=_run, args=(app, search_id), daemon=True)
    t.start()


def _set(conn, search_id, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE searches SET {cols} WHERE id = ?",
                 (*fields.values(), search_id))
    conn.commit()


def _run(app, search_id):
    cfg = app.config
    conn = standalone_connection(cfg["DATABASE"])
    s = None
    try:
        s = conn.execute("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
        if s is None:
            return
        user = conn.execute("SELECT * FROM users WHERE id = ?", (s["user_id"],)).fetchone()
        resume = None
        if s["resume_id"]:
            resume = conn.execute("SELECT * FROM resumes WHERE id = ?",
                                  (s["resume_id"],)).fetchone()
        resume_skills = json.loads(resume["skills"]) if resume and resume["skills"] else []
        profile = {}
        if resume and resume["profile"]:
            try:
                profile = json.loads(resume["profile"])
            except (ValueError, TypeError):
                profile = {}
        profile_terms = profile.get("terms", [])
        # industry-agnostic query for Google Jobs / JSearch, derived from the resume's
        # detected occupation. Computed from the stored resume text so it also applies
        # retroactively to resumes uploaded before role detection existed.
        resume_query = search_query(resume["text"], profile) if resume else ""
        # detected occupation(s) — used for field-agnostic scoring so non-tech jobs
        # rank on role match (stored on new uploads; derived from text retroactively).
        resume_roles = (profile.get("roles") or detect_roles(resume["text"])) if resume else []

        _set(conn, search_id, status="running",
             progress="Starting scan across public ATS boards…")

        def progress(done, total, msg):
            _set(conn, search_id, progress=msg)

        criteria = {
            "title_query": s["title_query"],
            "location": s["location"],
            "min_pay": s["min_pay"],
            "work_type": s["work_type"],
            "languages": s["languages"],
        }
        jobs, stats = run_search(
            criteria, resume_skills,
            max_per_provider=cfg["MAX_COMPANIES_PER_PROVIDER"],
            workers=cfg["FETCH_WORKERS"],
            timeout=cfg["FETCH_TIMEOUT"],
            query_timeout=cfg.get("QUERY_TIMEOUT", 30),
            progress=progress,
            profile_terms=profile_terms,
            resume_query=resume_query,
            resume_roles=resume_roles,
            serpapi_key=cfg.get("SERPAPI_KEY"),
            google_pages=cfg.get("GOOGLE_JOBS_PAGES", 1),
            jsearch_key=cfg.get("JSEARCH_KEY"),
            jsearch_pages=cfg.get("JSEARCH_PAGES", 1),
            time_limit=cfg.get("SEARCH_TIME_LIMIT", 120),
        )

        # persist jobs
        conn.execute("DELETE FROM jobs WHERE search_id = ?", (search_id,))
        for j in jobs:
            conn.execute(
                """INSERT INTO jobs (search_id, match_score, ats_score, role, company,
                   location, salary, post_status, work_type, why_matches, missing,
                   apply_link, source, remote_scope, flags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (search_id, j.match_score, j.ats_score, j.role, j.company, j.location,
                 j.salary or "Not stated", post_status(j), j.work_type,
                 j.why_matches, j.missing, j.apply_link, j.source, j.remote_scope,
                 json.dumps(j.flags)))
        conn.commit()

        # build the Excel export
        _set(conn, search_id, progress="Generating Excel workbook…")
        candidate = (user["full_name"] or user["email"].split("@")[0]).strip()
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        safe = "".join(c for c in candidate if c.isalnum() or c in " _-").strip().replace(" ", "_")
        fname = f"{safe or 'JobSearch'}_{stamp}.xlsx"
        out_path = str(Path(cfg["EXPORT_DIR"]) / fname)
        build_workbook(
            out_path, candidate_name=candidate, jobs=jobs,
            resume_skills=resume_skills, stats=stats, criteria=criteria,
            search_date=datetime.now().strftime("%Y-%m-%d"), profile=profile,
        )

        # structured report for the in-app full view
        report = build_report(jobs, resume_skills, profile, stats, criteria)
        _set(conn, search_id, report_json=json.dumps(report))

        note = f"Done — {stats['kept']} matches from {stats['raw']} scanned."
        if stats.get("google_status"):
            note += f"  |  Google Jobs: {stats['google_status']}"
        if stats.get("jsearch_status"):
            note += f"  |  JSearch: {stats['jsearch_status']}"
        # no value -> refund the credit
        if stats["kept"] == 0 and s["credit_source"] in ("free", "paid"):
            credits.refund_search(conn, s["user_id"], s["credit_source"])
            note += " No matches, so your credit was refunded."
        _set(conn, search_id, status="done", progress=note,
             result_count=stats["kept"], export_path=out_path,
             finished_at=datetime.now().isoformat(timespec="seconds"))
    except SearchTimeout:
        # exceeded the time limit -> cancel the search and refund the credit
        if s is not None and s["credit_source"] in ("free", "paid"):
            try:
                credits.refund_search(conn, s["user_id"], s["credit_source"])
            except Exception:
                pass
        _set(conn, search_id, status="error", result_count=0,
             error="Search exceeded the time limit and was cancelled.",
             progress="Search took too long — it was cancelled and your credit refunded.",
             finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        # failed run -> refund the credit
        if s is not None and s["credit_source"] in ("free", "paid"):
            try:
                credits.refund_search(conn, s["user_id"], s["credit_source"])
            except Exception:
                pass
        _set(conn, search_id, status="error", error=str(e),
             progress="Search failed — your credit was refunded.")
    finally:
        conn.close()
