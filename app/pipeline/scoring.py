"""Match scoring, ATS keyword scoring, and gap analysis."""
import math
import re

from . import geo, lang
from .keywords import extract_skills
from .profile import _ROLE_HEADS

_STOP = {"the", "and", "for", "with", "of", "to", "in", "a", "an", "or", "at",
         "senior", "sr", "jr", "junior", "staff", "lead", "principal", "ii", "iii"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]+", (s or "").lower())
            if w not in _STOP and len(w) > 1}


def parse_salary(text: str):
    """Best-effort (low, high) annual USD from a free-form salary string."""
    if not text:
        return None, None
    t = text.replace(",", "")
    nums = []
    for m in re.finditer(r"\$?\s*(\d+(?:\.\d+)?)\s*([kK])?", t):
        val = float(m.group(1))
        if m.group(2):  # K suffix
            val *= 1000
        # ignore stray small numbers (e.g. "2-5 years")
        if val >= 1000:
            nums.append(val)
    if not nums:
        return None, None
    # hourly heuristic: small numbers with /hr nearby
    if re.search(r"/\s*(hour|hr)\b", text, re.I) and max(nums) < 1000:
        nums = [n * 2080 for n in nums]
    lo, hi = min(nums), max(nums)
    return int(lo), int(hi)


# Generic role nouns carry little signal — matching only these shouldn't qualify.
_GENERIC = {"engineer", "developer", "manager", "analyst", "specialist", "associate",
            "consultant", "coordinator", "administrator", "director", "officer",
            "representative", "rep", "agent", "designer", "architect", "scientist"}


def title_relevance(query: str, role: str) -> float:
    """Weighted overlap of query terms in the role title.

    Distinctive words (e.g. 'implementation') count full; generic role nouns
    (e.g. 'engineer') count partial. If the query has distinctive words but none
    appear in the title, relevance is 0 — keeps results tightly on-target.
    """
    q = _tokens(query)
    if not q:
        return 0.6  # no title preference -> neutral-positive
    r = _tokens(role)
    if not r:
        return 0.0
    distinctive = q - _GENERIC
    if distinctive and not (distinctive & r):
        return 0.0
    weight = lambda w: 0.3 if w in _GENERIC else 1.0
    total = sum(weight(w) for w in q)
    matched = sum(weight(w) for w in (q & r))
    return matched / total if total else 0.0


_REMOTE_RE = re.compile(
    r"\bremote\b|work from home|\bwfh\b|work from anywhere|fully distributed|"
    r"remote[- ]first|distributed team", re.I)
_ONSITE_RE = re.compile(r"on[\s-]?site|in[\s-]?office|in[\s-]?person", re.I)


def work_type_ok(pref: str, job) -> bool:
    """Hard gate: does the posting satisfy the requested work type?

    Remote/Hybrid require positive evidence (drops plain city/onsite roles);
    On-site keeps anything not clearly remote-only.
    """
    pref = (pref or "any").lower().replace(" ", "")
    if pref in ("any", ""):
        return True
    blob = f"{job.work_type} {job.location} {(job.description or '')[:800]}"
    is_remote = bool(_REMOTE_RE.search(blob))
    is_hybrid = "hybrid" in blob.lower()
    if pref == "remote":
        return is_remote
    if pref == "hybrid":
        return is_hybrid
    if pref in ("on-site", "onsite"):
        return not is_remote or is_hybrid  # onsite or hybrid acceptable; pure-remote out
    return True


def work_type_fit(pref: str, job_work_type: str) -> float:
    pref = (pref or "any").lower()
    wt = (job_work_type or "").lower()
    if pref in ("any", ""):
        return 1.0
    if not wt:
        return 0.6  # unknown -> mild
    if pref in wt:
        return 1.0
    # remote pref but hybrid offered (or vice versa) -> partial
    return 0.4


def pay_fit(min_pay, job_low, job_high) -> float:
    if not min_pay:
        return 1.0
    if not job_high:
        return 0.7  # unknown salary -> mild benefit of the doubt
    if job_high >= min_pay:
        return 1.0
    # within 10% under target -> partial
    if job_high >= min_pay * 0.9:
        return 0.6
    return 0.2


def ats_score(resume_skills: set, jd_skills: set, title_rel: float) -> int:
    """ATS-style keyword match estimate (0..100)."""
    if not jd_skills:
        # nothing structured to match on; fall back to title alignment
        return int(round(55 + 30 * title_rel))
    overlap = len(resume_skills & jd_skills) / len(jd_skills)
    # blend keyword overlap (80%) with title alignment (20%)
    raw = 100 * (0.8 * overlap + 0.2 * title_rel)
    return int(round(max(5, min(99, raw))))


def geo_verdict(desired_geo, job):
    """Expose the location verdict for the pipeline's hard-drop gate."""
    return geo.assess(desired_geo, job)


def skill_alignment(profile_terms, jd_low: str) -> int:
    """Count how many of the candidate's own resume keywords appear in the JD."""
    hits = 0
    for t in profile_terms[:40]:   # bounded: keeps the hot loop cheap
        if len(t) < 4:
            continue
        if t in jd_low:
            hits += 1
    return hits


def _role_match(resume_roles, job_role, jd_low):
    """Field-agnostic match of the candidate's own occupation to a posting.

    Returns (fit 0..1, relevance_contribution, label). A role head-noun ('nurse',
    'accountant', 'electrician'…) in the posting's TITLE is a definitive field match
    (fit 1.0, +2 toward the skills-first gate); a body-only mention is weaker. This
    is the only positive signal a non-tech resume gets — the skill taxonomy is
    tech-only, so tax_overlap is always 0 for them.
    """
    words = set()
    for r in (resume_roles or []):
        words |= {w for w in re.findall(r"[a-z]+", r.lower())
                  if len(w) > 2 and w not in _STOP}
    heads = words & _ROLE_HEADS
    if not heads:
        return 0.0, 0, ""
    label = (resume_roles[0] or "").title()
    title_tokens = set(re.findall(r"[a-z]+", (job_role or "").lower()))
    if heads & title_tokens:
        return 1.0, 2, label
    if heads & set(re.findall(r"[a-z]+", jd_low)):
        return 0.6, 1, label
    return 0.0, 0, ""


def score_job(job, *, resume_skills, title_query, desired_geo, min_pay, work_type,
              spoken_languages, profile_terms=(), resume_roles=(), skill_weights=None,
              jd_skills=None):
    jd_text = job.description + " " + job.role
    jd_low = jd_text.lower()
    if jd_skills is None:
        jd_skills = extract_skills(jd_text)
    # rarity weight per taxonomy skill: ubiquitous skills (matched by nearly every
    # posting) count for little, distinctive skills count near 1. Falls back to a
    # flat 1.0 when no corpus weights are supplied.
    weights = skill_weights or {}
    skill_wt = lambda s: weights.get(s, 1.0)
    job_low, job_high = parse_salary(job.salary)
    has_title = bool((title_query or "").strip())

    t_rel = title_relevance(title_query, job.role)
    matched_skills = resume_skills & jd_skills
    tax_count = len(matched_skills)
    # weighted evidence of taxonomy overlap: sum the rarity weights of matched skills
    # and saturate. Matching two ubiquitous skills barely moves this; a few
    # distinctive ones (LLM Integration, RAG, n8n…) score high.
    matched_wt = sum(skill_wt(s) for s in matched_skills)
    tax_overlap = 1.0 - math.exp(-matched_wt / 1.5)
    term_hits = skill_alignment(profile_terms, jd_low)
    term_score = min(1.0, term_hits / 8.0)

    # resume-agnostic field signal — does the candidate's own occupation match this
    # posting? This is what carries non-tech resumes (nurse, cook, accountant…), for
    # which tax_overlap is always 0 because the skill taxonomy is tech-only.
    role_fit, role_hits, role_label = _role_match(resume_roles, job.role, jd_low)

    # Take the stronger of two paths so tech resumes keep their taxonomy-driven score
    # while non-tech resumes score on role + resume-term overlap. max() only raises,
    # so tech scoring never regresses; a cross-field job (nurse resume vs tech role)
    # matches neither path and stays low.
    tax_path = 0.6 * tax_overlap + 0.4 * term_score
    role_path = 0.7 * role_fit + 0.3 * term_score
    skill_component = max(tax_path, role_path)

    p_fit = pay_fit(min_pay, job_low, job_high)
    w_fit = work_type_fit(work_type, job.work_type)
    _, l_fit = geo.assess(desired_geo, job)
    job.remote_scope = geo.remote_scope_label(job)

    # languages — English is assumed as a baseline unless clearly the candidate's gap
    spoken = set(spoken_languages or set()) | {"English"}
    req_langs = lang.required_languages(jd_text)
    unmet = sorted(req_langs - spoken)
    matched_langs = sorted(req_langs & spoken)
    lang_fit = 1.0 if not req_langs else len(req_langs & spoken) / len(req_langs)

    # When no title is given, lean on the resume: skills/education/dev dominate.
    if has_title:
        w = dict(title=25, skill=35, pay=12, work=8, loc=12, lang=8)
    else:
        w = dict(title=0, skill=50, pay=14, work=9, loc=18, lang=9)

    match = (w["title"] * t_rel + w["skill"] * skill_component + w["pay"] * p_fit
             + w["work"] * w_fit + w["loc"] * l_fit + w["lang"] * lang_fit)
    job.match_score = int(round(max(1, min(99, match))))
    job.ats_score = ats_score(resume_skills, jd_skills, t_rel)
    # a clear role match counts toward the skills-first gate so field-relevant
    # non-tech jobs (no taxonomy skills) aren't dropped
    job.relevance_hits = tax_count + term_hits + role_hits

    # surface the most distinctive overlaps and gaps first (rarest skills lead)
    matched = sorted(matched_skills, key=lambda s: (-skill_wt(s), s))
    if not jd_skills:
        # non-tech posting: the tech taxonomy found nothing to compare, so don't
        # claim "strong keyword coverage" — point at the posting's own language
        missing_str = "Mirror this posting's own keywords in your resume."
    else:
        missing = sorted(jd_skills - resume_skills, key=lambda s: (-skill_wt(s), s))
        missing_str = ", ".join(missing) if missing else "None — strong keyword coverage"
    if unmet:
        missing_str += f"  |  Languages required: {', '.join(unmet)}"
        job.flags = list(job.flags) + [f"requires language: {', '.join(unmet)}"]
    job.missing = missing_str
    job.why_matches = _why(job, matched, has_title, t_rel, role_fit, role_label,
                           min_pay, job_high, matched_langs, term_hits)
    return job


def _why(job, matched, has_title, t_rel, role_fit, role_label, min_pay, job_high,
         matched_langs, term_hits=0) -> str:
    bits = []
    if has_title:
        if t_rel >= 0.6:
            bits.append("Title closely matches your target role")
        elif t_rel > 0:
            bits.append("Partial title alignment with your target")
    elif role_fit >= 1.0 and role_label:
        bits.append(f"Matches your field: {role_label}")
    elif role_fit > 0 and role_label:
        bits.append(f"Related to your field ({role_label})")
    if matched:
        bits.append("Overlapping skills: " + ", ".join(matched[:6]))
    if term_hits >= 3:
        bits.append(f"Aligns with {term_hits} keywords from your resume "
                    "(skills/education/development)")
    if min_pay and job_high:
        if job_high >= min_pay:
            bits.append(f"Pay meets target (up to ${job_high:,})")
        else:
            bits.append(f"Pay below target (tops out ${job_high:,})")
    if job.remote_scope:
        bits.append(job.remote_scope)
    elif job.work_type:
        bits.append(f"{job.work_type} role")
    if matched_langs and matched_langs != ["English"]:
        bits.append("Language match: " + ", ".join(matched_langs))
    if not bits:
        bits.append("General relevance to your profile")
    return ". ".join(bits) + "."
