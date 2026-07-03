"""Build the full structured report (the six-sheet content) as plain data.

Produced at search time from the ranked JobPosting list so the in-app report and
the Excel export show the same thing, and so the app doesn't need to keep full job
descriptions around afterward.
"""
from collections import Counter

from .keywords import extract_skills
from .scoring import parse_salary

_ACTIONS = {
    "Python": "Ship 2-3 Python projects to GitHub; add a production course.",
    "LLM Integration": "Build a small app on the Anthropic/OpenAI API for your portfolio.",
    "RAG / Vector DB": "Do a RAG course; build a doc-Q&A demo with a vector store.",
    "Docker": "Containerize one existing project; learn compose basics.",
    "Kubernetes": "Lower priority unless targeting infra roles.",
    "AWS": "Map your Azure experience to AWS; consider Cloud Practitioner.",
}


def _action(skill):
    return _ACTIONS.get(skill, f"Add {skill} via a focused course or portfolio project, "
                                "then surface it on your resume.")


def build_report(jobs, resume_skills, profile, stats, criteria):
    resume_skills = set(resume_skills or [])
    profile = profile or {}
    total = len(jobs)

    demand = Counter()
    companies = Counter()
    best_pay, best_job = -1, None
    for j in jobs:
        companies[j.company] += 1
        for sk in extract_skills((j.description or "") + " " + j.role):
            demand[sk] += 1
        _, hi = parse_salary(j.salary)
        if hi and hi > best_pay:
            best_pay, best_job = hi, j

    top_matches = [{
        "rank": i + 1, "company": j.company, "role": j.role,
        "match": j.match_score, "ats": j.ats_score, "why": j.why_matches,
        "salary": j.salary or "Not stated", "location": j.location,
        "work_type": j.work_type or "—", "remote_scope": j.remote_scope,
        "source": j.source, "missing": j.missing, "apply_link": j.apply_link,
    } for i, j in enumerate(jobs[:5])]

    gaps = [{"skill": sk, "demand": n} for sk, n in demand.most_common()
            if sk not in resume_skills]
    ats = {
        "avg": round(sum(j.ats_score for j in jobs) / total) if total else 0,
        "strengths": [{"skill": sk, "demand": demand.get(sk, 0)}
                      for sk in sorted(resume_skills)],
        "gaps": gaps[:12],
        "estimates": [{"company": j.company, "role": j.role, "ats": j.ats_score}
                      for j in jobs[:5]],
        "education": profile.get("education", [])[:8],
        "prof_dev": profile.get("prof_dev", [])[:8],
    }

    skill_gaps = [{"skill": sk, "freq": n, "total": total, "action": _action(sk)}
                  for sk, n in demand.most_common() if sk not in resume_skills][:12]

    strategy = {
        "today": [{"company": j.company, "role": j.role, "match": j.match_score,
                   "ats": j.ats_score, "apply_link": j.apply_link} for j in jobs[:2]],
        "week": [{"company": j.company, "role": j.role, "match": j.match_score,
                  "apply_link": j.apply_link} for j in jobs[2:5]],
        "resume_tips": [f"Mirror {j.company}'s language; surface these if you have them: "
                        f"{j.missing}" for j in jobs[:2]
                        if j.missing and "None" not in j.missing],
        "followups": [
            "Tailor your resume per role using the ATS Keywords section.",
            "Apply within 24-48h of a posting for best visibility.",
            "Track replies and follow up after 5-7 business days.",
            "Re-run this search every 2-3 days for fresh postings.",
        ],
    }

    boards = "Greenhouse · Lever · Ashby" + (
        " · Google Jobs" if stats.get("from_google") else "") + (
        " · LinkedIn/Indeed/ZipRecruiter (JSearch)" if stats.get("from_jsearch") else "")
    summary = {
        "total": total,
        "raw": stats.get("raw", 0),
        "dropped_quality": stats.get("dropped_quality", 0),
        "dropped_worktype": stats.get("dropped_worktype", 0),
        "dropped_location": stats.get("dropped_location", 0),
        "dropped_relevance": stats.get("dropped_relevance", 0),
        "dropped_duplicate": stats.get("dropped_duplicate", 0),
        "from_google": stats.get("from_google", 0),
        "google_status": stats.get("google_status", ""),
        "top_companies": [c for c, _ in companies.most_common(5)],
        "common_skills": [s for s, _ in demand.most_common(6)],
        "best_pay": (f"{best_job.company} — {best_job.role} (up to ${best_pay:,})"
                     if best_job else "Not stated in postings"),
        "top_match": (f"{jobs[0].company} — {jobs[0].role} (score {jobs[0].match_score})"
                      if jobs else "—"),
        "boards": boards,
    }

    return {"summary": summary, "top_matches": top_matches, "ats": ats,
            "skill_gaps": skill_gaps, "strategy": strategy}
