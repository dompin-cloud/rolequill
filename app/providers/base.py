"""Shared job-posting model and provider base class."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

_MIN_KEYS = ("min_value", "minValue", "min", "min_cents", "minimum", "low")
_MAX_KEYS = ("max_value", "maxValue", "max", "max_cents", "maximum", "high")
_CUR_KEYS = ("unit", "currency", "currencyCode", "currency_type", "currencyType")


class SearchTimeout(Exception):
    """Raised when a search exceeds its overall wall-clock time limit."""


def _num(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            try:
                v = float(d[k])
                if "cent" in k:  # *_cents are in cents
                    v /= 100.0
                return v
            except (TypeError, ValueError):
                continue
    return None


def format_pay(value) -> str:
    """Turn any salary shape (dict, dict-repr string, plain string) into clean text
    like '$81,000 - $119,000 USD'. Prevents raw dicts leaking into the UI/Excel."""
    if value is None:
        return ""
    # dict-repr that arrived as a string, e.g. "{'unit': 'USD', ...}"
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                value = ast.literal_eval(s)
            except (ValueError, SyntaxError):
                return s
        else:
            return s
    if isinstance(value, (list, tuple)):
        for item in value:
            out = format_pay(item)
            if out:
                return out
        return ""
    if isinstance(value, dict):
        lo, hi = _num(value, _MIN_KEYS), _num(value, _MAX_KEYS)
        cur = next((str(value[k]) for k in _CUR_KEYS if value.get(k)), "USD")
        if lo and hi:
            return f"${lo:,.0f} - ${hi:,.0f} {cur}".strip()
        if hi or lo:
            return f"${(hi or lo):,.0f} {cur}".strip()
        return ""
    return str(value)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ]+")


def html_to_text(html: str) -> str:
    if not html:
        return ""
    text = html.replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n")
    text = _TAG_RE.sub(" ", text)
    # unescape the handful of entities ATS feeds actually emit
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&#39;", "'"), ("&quot;", '"'), ("&nbsp;", " ")):
        text = text.replace(a, b)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass
class JobPosting:
    source: str                      # 'Greenhouse' / 'Lever' / 'Ashby'
    company: str
    role: str
    apply_link: str
    location: str = ""
    work_type: str = ""              # Remote / Hybrid / On-site / ""
    salary: str = ""
    description: str = ""
    posted_at: Optional[datetime] = None
    job_id: str = ""
    raw_status: str = ""             # e.g. 'open', 'listed' if the feed says so
    # populated by the pipeline:
    remote_scope: str = ""           # human-readable allowed remote locations
    match_score: int = 0
    ats_score: int = 0
    relevance_hits: int = 0          # skill/term overlap count (for skills-first gate)
    why_matches: str = ""
    missing: str = ""
    flags: list = field(default_factory=list)

    # cap stored JD text — keyword/skill/ATS matching only needs the early sections,
    # and full JDs (some many KB each, thousands per search) blow up memory on small hosts
    DESC_CAP = 2500

    def __post_init__(self):
        # safety net: never let a structured pay object reach the UI/Excel
        if not isinstance(self.salary, str):
            self.salary = format_pay(self.salary)
        elif self.salary.strip().startswith(("{", "[")):
            self.salary = format_pay(self.salary)
        if self.description and len(self.description) > self.DESC_CAP:
            self.description = self.description[:self.DESC_CAP]

    @property
    def age_days(self) -> Optional[float]:
        if not self.posted_at:
            return None
        now = datetime.now(timezone.utc)
        dt = self.posted_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds() / 86400.0


def parse_iso(value) -> Optional[datetime]:
    """Best-effort timestamp parsing across the three feeds (ISO strings or epoch ms)."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            # Lever uses epoch milliseconds
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        s = str(value).strip()
        if not s:
            return None
        if s.isdigit():
            return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError):
        return None


def detect_work_type(*texts) -> str:
    blob = " ".join(t for t in texts if t).lower()
    if re.search(r"\bhybrid\b", blob):
        return "Hybrid"
    if re.search(r"\bremote\b|work from home|wfh|distributed", blob):
        return "Remote"
    if re.search(r"\bon[\s-]?site\b|in[\s-]?office|in[\s-]?person", blob):
        return "On-site"
    return ""


class Provider:
    name = "base"

    def __init__(self, session, timeout=12):
        self.session = session
        self.timeout = timeout

    def fetch(self, company_token: str) -> list[JobPosting]:
        raise NotImplementedError
