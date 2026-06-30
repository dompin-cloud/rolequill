"""End-to-end search pipeline: fetch -> filter -> score -> rank."""
from . import geo, lang
from ..providers import fetch_all, fetch_query_sources
from .filters import classify
from .scoring import geo_verdict, score_job, title_relevance, work_type_ok

RESULT_LIMIT = 40
MATCH_FLOOR = 45          # drop weak matches below this score
MIN_TITLE_OVERLAP = 0.34  # when a title query is given, require some token overlap
SKILL_FLOOR = 2           # with no title, require >=N resume-skill/keyword overlaps


def run_search(criteria: dict, resume_skills, *, max_per_provider, workers,
               timeout, progress=None, profile_terms=(), serpapi_key=None,
               google_pages=1):
    """criteria: title_query, location, min_pay, work_type, languages.
    Returns (ranked_jobs, stats)."""
    resume_skills = set(resume_skills or [])
    profile_terms = list(profile_terms or [])
    title_query = criteria.get("title_query") or ""
    has_title = bool(title_query.strip())
    location = criteria.get("location") or ""
    min_pay = criteria.get("min_pay") or 0
    work_type = criteria.get("work_type") or "any"
    spoken_languages = lang.parse_spoken(criteria.get("languages") or "")
    desired_geo = geo.desired_regions(location)

    raw = fetch_all(max_per_provider=max_per_provider, workers=workers,
                    timeout=timeout, progress=progress)

    # query-based sources (Google Jobs) — title, else top resume skills/terms.
    # Keep the query broad: appending the raw location ("Remote USA") over-constrains
    # Google and returns nothing. Just nudge "remote" when relevant; our own geo +
    # work-type filters narrow the results afterward.
    google_q = (title_query.strip()
                or " ".join(profile_terms[:4])
                or " ".join(sorted(resume_skills)[:4]))
    if google_q and work_type in ("remote", "hybrid"):
        google_q = f"{google_q} {work_type}"
    google, google_status = fetch_query_sources(
        google_q, "", serpapi_key=serpapi_key, pages=google_pages,
        timeout=timeout, progress=progress)
    raw.extend(google)

    stats = {"raw": len(raw), "from_google": len(google),
             "google_status": google_status, "dropped_quality": 0,
             "dropped_relevance": 0, "dropped_location": 0,
             "dropped_worktype": 0, "kept": 0}

    if progress:
        progress(100, 100, f"Filtering & scoring {len(raw)} postings…")

    seen = set()
    scored = []
    total = len(raw)
    for idx, job in enumerate(raw):
        if progress and idx and idx % 500 == 0:
            progress(idx, total, f"Filtering & scoring {idx}/{total} postings…")
        key = (job.company.lower().strip(), job.role.lower().strip())
        if key in seen:
            continue
        seen.add(key)

        keep, reasons = classify(job)
        if not keep:
            stats["dropped_quality"] += 1
            continue

        # relevance gate on title query (avoids returning every open req)
        if has_title and title_relevance(title_query, job.role) < MIN_TITLE_OVERLAP:
            stats["dropped_relevance"] += 1
            continue

        # work-type gate: honor Remote/Hybrid/On-site as a hard filter
        if not work_type_ok(work_type, job):
            stats["dropped_worktype"] += 1
            continue

        # location gate: drop postings whose stated remote scope conflicts with
        # the user's desired country (e.g. "Remote Japan" vs a "Remote USA" search)
        if desired_geo:
            verdict, _ = geo_verdict(desired_geo, job)
            if verdict == "conflict":
                stats["dropped_location"] += 1
                continue

        job.flags = reasons
        score_job(job, resume_skills=resume_skills, title_query=title_query,
                  desired_geo=desired_geo, min_pay=min_pay, work_type=work_type,
                  spoken_languages=spoken_languages, profile_terms=profile_terms)
        # skills-first gate: with no title, require real overlap with the resume
        if not has_title and job.relevance_hits < SKILL_FLOOR:
            stats["dropped_relevance"] += 1
            continue
        if job.match_score < MATCH_FLOOR:
            stats["dropped_relevance"] += 1
            continue
        scored.append(job)

    scored.sort(key=lambda j: j.match_score, reverse=True)
    ranked = scored[:RESULT_LIMIT]
    stats["kept"] = len(ranked)
    return ranked, stats
