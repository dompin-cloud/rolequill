"""Ashby public job board API (free, no key).

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true
"""
from .base import JobPosting, Provider, detect_work_type, html_to_text, parse_iso

API = ("https://api.ashbyhq.com/posting-api/job-board/"
       "{token}?includeCompensation=true")


class AshbyProvider(Provider):
    name = "Ashby"

    def fetch(self, company_token: str) -> list[JobPosting]:
        url = API.format(token=company_token)
        resp = self.session.get(url, timeout=self.timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        out = []
        for j in data.get("jobs", []):
            # Ashby only lists live jobs, but respect isListed when present.
            if j.get("isListed") is False:
                continue
            desc = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))
            loc = j.get("location", "") or ""
            remote = j.get("isRemote")
            wt = ("Remote" if remote else "") or detect_work_type(
                loc, j.get("employmentType", ""), desc[:600])
            out.append(JobPosting(
                source=self.name,
                company=j.get("organizationName") or company_token.title(),
                role=j.get("title", "").strip(),
                apply_link=j.get("jobUrl", "") or j.get("applyUrl", ""),
                location=loc,
                work_type=wt,
                salary=_extract_salary(j),
                description=desc,
                posted_at=parse_iso(j.get("publishedAt") or j.get("updatedAt")),
                job_id=str(j.get("id", "")),
                raw_status="open",
            ))
        return out


def _extract_salary(j) -> str:
    comp = j.get("compensation") or {}
    summary = comp.get("compensationTierSummary")
    if summary:
        return str(summary)
    tiers = comp.get("summaryComponents") or []
    for t in tiers:
        if t.get("compensationType") == "Salary":
            lo, hi = t.get("minValue"), t.get("maxValue")
            cur = t.get("currencyCode", "USD")
            if lo and hi:
                return f"${lo:,.0f} - ${hi:,.0f} {cur}".strip()
    return ""
