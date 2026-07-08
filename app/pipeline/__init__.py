"""End-to-end search pipeline: fetch -> filter -> score -> rank."""
import math
import re
import time
from collections import Counter

from . import geo, lang
from ..providers import fetch_all, fetch_jsearch_source, fetch_query_sources
from ..providers.base import SearchTimeout
from .filters import classify
from .keywords import extract_skills
from .scoring import geo_verdict, score_job, title_relevance, work_type_ok

RESULT_LIMIT = 40
MAX_PER_COMPANY = 3       # cap listings per employer so one big ATS board can't flood
MATCH_FLOOR = 45          # drop weak matches below this score
MIN_TITLE_OVERLAP = 0.34  # when a title query is given, require some token overlap
SKILL_FLOOR = 2           # with no title, require >=N resume-skill/keyword overlaps

# direct-ATS sources are preferred when a duplicate spans multiple boards, so the
# kept listing links straight to the employer's application page
DIRECT_SOURCES = {"Greenhouse", "Lever", "Ashby"}

_CO_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|co|corp|corporation|gmbh|plc|sa|ag|pty|group|holdings)\b")
_ROLE_NOISE = re.compile(
    r"\b(remote|hybrid|on-?site|onsite|full-?time|part-?time|contract|permanent)\b")
_ABBR = {"sr": "senior", "jr": "junior", "mgr": "manager"}


def _norm_company(c):
    c = _CO_SUFFIX.sub(" ", re.sub(r"[^a-z0-9 ]", " ", (c or "").lower()))
    return re.sub(r"\s+", " ", c).strip()


def _norm_role(r):
    r = re.sub(r"\(.*?\)", " ", (r or "").lower())        # drop parentheticals
    r = _ROLE_NOISE.sub(" ", r)                            # drop remote/full-time/etc.
    r = re.sub(r"[^a-z0-9 ]", " ", r)
    return " ".join(_ABBR.get(t, t) for t in r.split()).strip()


def dedup_key(job):
    """Cross-source key so the same role from different boards collapses to one."""
    return (_norm_company(job.company), _norm_role(job.role))


def _skill_weights(jd_skill_sets):
    """IDF-style rarity weights in ~(0,1] over the current candidate pool: a skill
    present in almost every posting scores ~0, a skill in only one scores near 1.
    Lets scoring reward distinctive overlap and discount ubiquitous skills."""
    n = len(jd_skill_sets)
    if not n:
        return {}
    df = Counter()
    for skills in jd_skill_sets:
        df.update(skills)
    denom = math.log(n + 1)
    return {s: math.log((n + 1) / (c + 0.5)) / denom for s, c in df.items()}


def _cap_per_company(scored, limit, per_company):
    """Keep at most `per_company` listings per employer (best-first), backfilling
    from the overflow only if capping would otherwise leave us short of `limit`."""
    kept, overflow, counts = [], [], Counter()
    for job in scored:                       # scored is already best-first
        c = _norm_company(job.company)
        if counts[c] < per_company:
            kept.append(job)
            counts[c] += 1
            if len(kept) >= limit:
                return kept[:limit]
        else:
            overflow.append(job)
    for job in overflow:                     # short of limit — top up with the rest
        if len(kept) >= limit:
            break
        kept.append(job)
    return kept[:limit]


def run_search(criteria: dict, resume_skills, *, max_per_provider, workers,
               timeout, query_timeout=None, progress=None, profile_terms=(),
               resume_query="", resume_roles=(), serpapi_key=None, google_pages=1,
               jsearch_key=None, jsearch_pages=1, time_limit=None):
    """criteria: title_query, location, min_pay, work_type, languages.
    Returns (ranked_jobs, stats). Raises SearchTimeout if it exceeds time_limit secs."""
    deadline = (time.monotonic() + time_limit) if time_limit else None
    # query aggregators get a larger per-call ceiling than the per-board ATS calls
    query_timeout = query_timeout or timeout
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
                    timeout=timeout, progress=progress, deadline=deadline)

    # query-based sources (Google Jobs / JSearch) — these are industry-agnostic, so
    # they're how a non-tech resume finds relevant work (the ATS roster is tech-only).
    # Priority: the user's typed title > the resume's detected occupation (role-first,
    # field-agnostic) > raw profile terms > skills. Keep it broad: appending the raw
    # location over-constrains Google; our geo + work-type filters narrow afterwards.
    google_q = (title_query.strip()
                or (resume_query or "").strip()
                or " ".join(profile_terms[:4])
                or " ".join(sorted(resume_skills)[:4]))
    if google_q and work_type in ("remote", "hybrid"):
        google_q = f"{google_q} {work_type}"
    google, google_status = fetch_query_sources(
        google_q, "", serpapi_key=serpapi_key, pages=google_pages,
        timeout=query_timeout, progress=progress, deadline=deadline)
    raw.extend(google)

    # JSearch (LinkedIn / Indeed / ZipRecruiter …) — same query, geo/remote hints
    jsearch, jsearch_status = fetch_jsearch_source(
        google_q, jsearch_key=jsearch_key, pages=jsearch_pages,
        country=("us" if "US" in desired_geo else None),
        remote_only=(work_type == "remote"), timeout=query_timeout,
        progress=progress, deadline=deadline)
    raw.extend(jsearch)

    stats = {"raw": len(raw), "from_google": len(google),
             "from_jsearch": len(jsearch), "google_status": google_status,
             "jsearch_status": jsearch_status, "dropped_quality": 0,
             "dropped_relevance": 0, "dropped_location": 0,
             "dropped_worktype": 0, "dropped_duplicate": 0, "kept": 0}

    # process direct-ATS listings first so a cross-board duplicate keeps the direct
    # apply link (stable sort preserves each provider's original ordering)
    raw.sort(key=lambda j: 0 if j.source in DIRECT_SOURCES else 1)

    if progress:
        progress(100, 100, f"Filtering & scoring {len(raw)} postings…")

    # ---- pass 1: dedupe + hard gates; stash JD skills for the corpus weighting ----
    seen = set()
    survivors = []
    total = len(raw)
    for idx, job in enumerate(raw):
        if idx % 250 == 0:
            if deadline and time.monotonic() > deadline:
                raise SearchTimeout("scoring phase exceeded time limit")
            if progress and idx:
                progress(idx, total, f"Filtering & scoring {idx}/{total} postings…")
        key = dedup_key(job)
        if key in seen:
            stats["dropped_duplicate"] += 1
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
        job._jd_skills = extract_skills(job.description + " " + job.role)
        survivors.append(job)

    # rarity weights across the survivors so ubiquitous skills stop inflating scores
    weights = _skill_weights([j._jd_skills for j in survivors])

    # ---- pass 2: score with the corpus weights + apply the skill/score floors ----
    scored = []
    for job in survivors:
        score_job(job, resume_skills=resume_skills, title_query=title_query,
                  desired_geo=desired_geo, min_pay=min_pay, work_type=work_type,
                  spoken_languages=spoken_languages, profile_terms=profile_terms,
                  resume_roles=resume_roles, skill_weights=weights,
                  jd_skills=job._jd_skills)
        # skills-first gate: with no title, require real overlap with the resume
        if not has_title and job.relevance_hits < SKILL_FLOOR:
            stats["dropped_relevance"] += 1
            continue
        if job.match_score < MATCH_FLOOR:
            stats["dropped_relevance"] += 1
            continue
        scored.append(job)

    scored.sort(key=lambda j: j.match_score, reverse=True)
    ranked = _cap_per_company(scored, RESULT_LIMIT, MAX_PER_COMPANY)
    stats["kept"] = len(ranked)
    return ranked, stats
