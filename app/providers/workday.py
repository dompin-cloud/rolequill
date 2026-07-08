"""Workday public 'CXS' careers API (free, no key).

Large non-tech employers — hospitals, retailers, universities, banks, manufacturers —
host their own Workday tenant. Unlike Greenhouse/Lever/Ashby (tech-skewed), Workday is
where the bulk of nursing, pharmacy, retail, finance-ops, and skilled roles actually
live. The public careers endpoint:

    POST https://{host}/wday/cxs/{tenant}/{site}/jobs
         body {"limit":N,"offset":N,"searchText":<q>,"appliedFacets":{}}

returns a page of postings (list view: title / location / relative post date — no
description). We pass the resume-derived query as `searchText` so each big tenant
(CVS has 17k+ reqs) returns *relevant* roles rather than an arbitrary slice.

Company tokens are encoded 'host|tenant|site' in providers/companies.py.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from .base import JobPosting, Provider, detect_work_type

_PAGE = 20
_MAX_PAGES = 2          # ~40 postings per tenant; keeps the fan-out within the time budget


def _posted(text):
    """Workday 'postedOn' is human text ('Posted 5 Days Ago', 'Posted Today',
    'Posted 30+ Days Ago') — turn it into an approximate UTC datetime for staleness."""
    s = (text or "").lower()
    now = datetime.now(timezone.utc)
    if "today" in s or "just posted" in s:
        return now
    if "yesterday" in s:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*day", s)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\+?\s*month", s)
    if m:
        return now - timedelta(days=30 * int(m.group(1)))
    return None


class WorkdayProvider(Provider):
    name = "Workday"

    def fetch(self, token: str, query: str = "") -> list[JobPosting]:
        parts = (token or "").split("|")
        if len(parts) != 3:
            return []
        host, tenant, site = (p.strip() for p in parts)
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        company = tenant.replace("-", " ").replace("_", " ").title()
        out = []
        for page in range(_MAX_PAGES):
            body = {"limit": _PAGE, "offset": page * _PAGE,
                    "searchText": (query or "")[:100], "appliedFacets": {}}
            try:
                resp = self.session.post(
                    url, data=json.dumps(body), timeout=self.timeout,
                    headers={"Content-Type": "application/json"})
            except Exception:
                break
            if resp.status_code != 200:
                break
            try:
                postings = resp.json().get("jobPostings", [])
            except ValueError:
                break
            if not postings:
                break
            for j in postings:
                ext = (j.get("externalPath") or "").strip()
                link = f"https://{host}/{site}{ext}" if ext else f"https://{host}/{site}"
                loc = (j.get("locationsText") or "").strip()
                out.append(JobPosting(
                    source=self.name,
                    company=company,
                    role=(j.get("title") or "").strip(),
                    apply_link=link,
                    location=loc,
                    work_type=detect_work_type(loc, "", ""),
                    salary="",
                    description="",   # list view has no body; role/title drives matching
                    posted_at=_posted(j.get("postedOn")),
                    job_id=str(j.get("bulletFields", [""])[0]) if j.get("bulletFields") else "",
                    raw_status="open",
                ))
            if len(postings) < _PAGE:
                break
        return out
