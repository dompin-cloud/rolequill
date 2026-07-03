"""JSearch (RapidAPI) — aggregated jobs from LinkedIn, Indeed, ZipRecruiter, etc.

Query-based, like Google Jobs. Uses the /search endpoint. Disabled unless
JSEARCH_KEY (your RapidAPI key) is set.

Get a key: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
Docs: /search params — query, page, num_pages, country, date_posted, remote_jobs_only
"""
from datetime import datetime, timezone

from .base import JobPosting, detect_work_type, parse_iso

ENDPOINT = "https://jsearch.p.rapidapi.com/search-v2"   # JSearch v5 search endpoint
HOST = "jsearch.p.rapidapi.com"


class JSearchProvider:
    name = "JSearch"

    def __init__(self, session, api_key, timeout=15, pages=1):
        self.session = session
        self.api_key = api_key
        self.timeout = timeout
        self.pages = max(1, pages)

    def search(self, query, country=None, remote_only=False, date_posted="all"):
        if not (self.api_key and query):
            return []
        # remote_only is folded into the query text (v5 /search-v2 has no such param)
        headers = {"X-RapidAPI-Key": self.api_key, "X-RapidAPI-Host": HOST}
        params = {"query": query.strip(), "num_pages": str(self.pages),
                  "date_posted": date_posted}
        if country:
            params["country"] = country

        resp = self.session.get(ENDPOINT, headers=headers, params=params,
                                timeout=self.timeout)
        data = {}
        try:
            data = resp.json()
        except ValueError:
            pass
        if resp.status_code != 200 or str(data.get("status", "")).upper() not in ("OK", ""):
            msg = data.get("message") or data.get("error") or f"HTTP {resp.status_code}"
            raise RuntimeError(str(msg)[:200])

        # v5 /search-v2 nests results under data.jobs; older shape was a flat list
        container = data.get("data")
        if isinstance(container, dict):
            jobs_list = container.get("jobs") or []
        elif isinstance(container, list):
            jobs_list = container
        else:
            jobs_list = []

        out = []
        for j in jobs_list:
            posting = self._to_posting(j)
            if posting:
                out.append(posting)
        return out

    def _to_posting(self, j):
        # prefer a direct-to-employer apply option
        apply_link, publisher = "", j.get("job_publisher") or "JSearch"
        options = j.get("apply_options") or []
        best = next((o for o in options if o.get("is_direct")), None) or \
            (options[0] if options else None)
        if best:
            apply_link = best.get("apply_link") or ""
            publisher = best.get("publisher") or publisher
        apply_link = apply_link or j.get("job_apply_link") or ""
        if not apply_link:
            return None

        loc = ", ".join(p for p in (j.get("job_city"), j.get("job_state"),
                                    j.get("job_country")) if p)
        remote = bool(j.get("job_is_remote"))
        if remote and "remote" not in loc.lower():
            loc = (f"Remote — {loc}" if loc else "Remote")
        desc = j.get("job_description") or ""
        work_type = ("Remote" if remote else "") or detect_work_type(
            loc, j.get("job_employment_type", ""), desc[:600])

        return JobPosting(
            source=publisher,                       # LinkedIn / Indeed / ZipRecruiter…
            company=(j.get("employer_name") or "").strip() or "Unknown",
            role=(j.get("job_title") or "").strip(),
            apply_link=apply_link,
            location=loc,
            work_type=work_type,
            salary=_salary(j),
            description=desc,
            posted_at=parse_iso(j.get("job_posted_at_datetime_utc")),
            job_id=str(j.get("job_id", ""))[:120],
            raw_status="open",
        )


def _salary(j):
    lo, hi = j.get("job_min_salary"), j.get("job_max_salary")
    cur = j.get("job_salary_currency") or "USD"
    period = (j.get("job_salary_period") or "").upper()
    if not (lo or hi):
        return ""
    unit = "/hr" if period == "HOUR" else ("/mo" if period == "MONTH" else "")
    try:
        if lo and hi:
            return f"${float(lo):,.0f} - ${float(hi):,.0f}{unit} {cur}".strip()
        return f"${float(hi or lo):,.0f}{unit} {cur}".strip()
    except (TypeError, ValueError):
        return ""
