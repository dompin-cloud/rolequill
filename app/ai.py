"""AI features via the Anthropic Messages API (uses `requests`, no new deps).

All AI is OPTIONAL and best-effort. If ANTHROPIC_API_KEY isn't set, every entry
point degrades to None so callers fall back to the non-AI behavior instead of
crashing — exactly like mailer.py does for email. No call here ever raises; a
network error, a bad key, or an unparseable reply all return None.

Raw HTTPS through `requests` (not the anthropic SDK) is deliberate: it matches the
mailer, adds zero dependencies, and keeps the single-worker 512MB Render footprint
flat (no pydantic/httpx runtime). These are simple one-shot Messages calls.
"""
import json
import re

import requests
from flask import current_app

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


def is_enabled():
    return bool(current_app.config.get("ANTHROPIC_API_KEY"))


def _message(model, system, user, *, max_tokens=1024, timeout=20):
    """One-shot Messages API call. Returns the assistant text, or None on any failure."""
    key = current_app.config.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    try:
        r = requests.post(
            _ENDPOINT, json=payload, timeout=timeout,
            headers={
                "x-api-key": key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            })
        if r.status_code >= 400:
            current_app.logger.error("Anthropic call failed (%s): %s",
                                     r.status_code, r.text[:300])
            return None
        data = r.json()
        # content is a list of blocks; concatenate the text blocks
        parts = [b.get("text", "") for b in data.get("content", [])
                 if b.get("type") == "text"]
        text = "".join(parts).strip()
        return text or None
    except (requests.RequestException, ValueError) as e:  # network / bad JSON envelope
        current_app.logger.error("Anthropic call error: %s", e)
        return None


def _extract_json(text):
    """Best-effort parse of a JSON object from a model reply. Tolerates ```json
    fences and leading/trailing prose. Returns a dict, or None."""
    if not text:
        return None
    # strip a fenced code block if present
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidate = fence.group(1) if fence else text
    # fall back to the first balanced-looking {...} span
    if not fence:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start:end + 1]
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


def _str_list(val, *, cap=6, maxlen=200):
    """Coerce a model value into a clean list of short strings (defensive)."""
    if isinstance(val, str):
        val = [val]
    if not isinstance(val, list):
        return []
    out = []
    for item in val:
        s = str(item).strip()
        if s:
            out.append(s[:maxlen])
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------------------
# Feature 1: resume analysis & coaching (one call per upload; off the search path)
# ---------------------------------------------------------------------------

_RESUME_SYSTEM = (
    "You are an expert career coach and resume analyst. You read a candidate's resume "
    "and return a concise, honest, field-agnostic assessment. Work for ANY occupation "
    "(nurse, welder, accountant, software engineer, teacher…), never assume tech. "
    "Respond with ONLY a JSON object, no prose, no code fence, matching exactly:\n"
    "{\n"
    '  "target_roles": [3-6 job titles this resume is competitive for, most fitting first],\n'
    '  "seniority": "one short phrase, e.g. entry-level / mid-level / senior / lead / director",\n'
    '  "strengths": [3-5 short phrases: the resume\'s strongest selling points],\n'
    '  "gaps": [3-5 short, ACTIONABLE improvements — missing skills, weak sections, quantification],\n'
    '  "summary": "2-3 sentence coaching paragraph in second person (\'You…\')"\n'
    "}"
)


def resume_insights(resume_text):
    """Analyze a resume. Returns a dict (target_roles, seniority, strengths, gaps,
    summary) or None if AI is disabled/unavailable. Never raises.

    Runs at UPLOAD time, not during a search, so it never competes with the 120s
    search budget."""
    if not is_enabled() or not (resume_text or "").strip():
        return None
    # bound input cost: ~6000 chars ≈ 1500 tokens is plenty of a resume to assess
    user = "Analyze this resume:\n\n" + resume_text[:6000]
    model = current_app.config.get("AI_MODEL_ANALYSIS", "claude-sonnet-5")
    raw = _message(model, _RESUME_SYSTEM, user, max_tokens=900)
    obj = _extract_json(raw)
    if not obj:
        return None
    insights = {
        "target_roles": _str_list(obj.get("target_roles"), cap=6, maxlen=80),
        "seniority": str(obj.get("seniority", "")).strip()[:60],
        "strengths": _str_list(obj.get("strengths"), cap=5, maxlen=200),
        "gaps": _str_list(obj.get("gaps"), cap=5, maxlen=200),
        "summary": str(obj.get("summary", "")).strip()[:600],
    }
    # require at least some signal, else treat as a miss
    if not (insights["target_roles"] or insights["strengths"] or insights["summary"]):
        return None
    return insights
