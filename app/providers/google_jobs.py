"""Google Jobs via SerpApi (query-based, paid — free tier 100 searches/mo).

Unlike the ATS providers (per-company), this is a single keyword+location search
against Google's aggregated job index. Disabled unless SERPAPI_KEY is configured.

Get a key at https://serpapi.com (free tier). Set env SERPAPI_KEY=...
Docs: https://serpapi.com/google-jobs-api
"""
import re
from datetime import datetime, timedelta, timezone

from .base import JobPosting, detect_work_type, format_pay

ENDPOINT = "https://serpapi.com/search.json"

_REL_RE = re.compile(r"(\d+)\s*(hour|day|week|month|minute)s?\s*ago", re.I)


def _parse_relative(text):
    """'3 days ago' -> approximate UTC datetime."""
    if not text:
        return None
    m = _REL_RE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    delta = {
        "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "week": timedelta(weeks=n),
        "month": timedelta(days=30 * n),
    }.get(unit)
    return datetime.now(timezone.utc) - delta if delta else None


class GoogleJobsProvider:
    name = "Google Jobs"

    def __init__(self, session, api_key, timeout=15, pages=1):
        self.session = session
        self.api_key = api_key
        self.timeout = timeout
        self.pages = max(1, pages)

    def search(self, query, location=""):
        """Return JobPosting list for a keyword query. Each page = 1 SerpApi credit."""
        if not (self.api_key and query):
            return []
        q = query.strip()
        if location:
            q = f"{q} {location}".strip()

        out = []
        next_token = None
        for page in range(self.pages):
            params = {"engine": "google_jobs", "q": q, "api_key": self.api_key,
                      "hl": "en"}
            if next_token:
                params["next_page_token"] = next_token
            resp = self.session.get(ENDPOINT, params=params, timeout=self.timeout)
            try:
                data = resp.json()
            except ValueError:
                data = {}
            # "no results" is normal (narrow query), not a failure — return empty.
            err = (data.get("error") or "").lower()
            if "hasn't returned" in err or "has not returned" in err or "no results" in err:
                break
            # Surface real SerpApi failures (bad key, exhausted quota, bad params).
            if resp.status_code != 200 or data.get("error"):
                msg = data.get("error") or f"HTTP {resp.status_code}"
                if page == 0:
                    raise RuntimeError(msg)
                break
            for j in data.get("jobs_results", []):
                out.append(self._to_posting(j))
            next_token = (data.get("serpapi_pagination") or {}).get("next_page_token")
            if not next_token:
                break
        return [p for p in out if p]

    def _to_posting(self, j):
        ext = j.get("detected_extensions") or {}
        # best apply link
        apply_link = ""
        for opt in (j.get("apply_options") or []):
            if opt.get("link"):
                apply_link = opt["link"]
                break
        apply_link = apply_link or j.get("share_link", "")
        if not apply_link:
            return None

        desc = j.get("description", "") or ""
        loc = j.get("location", "") or ""
        wfh = ext.get("work_from_home")
        work_type = ("Remote" if wfh else "") or detect_work_type(
            loc, ext.get("schedule_type", ""), desc[:600])
        via = (j.get("via") or "").replace("via ", "").strip()

        return JobPosting(
            source=f"Google ({via})" if via else "Google Jobs",
            company=j.get("company_name", "").strip() or "Unknown",
            role=j.get("title", "").strip(),
            apply_link=apply_link,
            location=loc,
            work_type=work_type,
            salary=format_pay(ext.get("salary", "")),
            description=desc,
            posted_at=_parse_relative(ext.get("posted_at")),
            job_id=str(j.get("job_id", ""))[:120],
            raw_status="open",
        )
