"""Lever public postings API (free, no key).

Endpoint: https://api.lever.co/v0/postings/{token}?mode=json
"""
from .base import JobPosting, Provider, detect_work_type, parse_iso

API = "https://api.lever.co/v0/postings/{token}?mode=json"


class LeverProvider(Provider):
    name = "Lever"

    def fetch(self, company_token: str) -> list[JobPosting]:
        url = API.format(token=company_token)
        resp = self.session.get(url, timeout=self.timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        out = []
        for j in data:
            cats = j.get("categories") or {}
            loc = cats.get("location", "") or ""
            commitment = cats.get("commitment", "") or ""
            workplace = j.get("workplaceType", "") or ""  # remote/hybrid/on-site
            desc = j.get("descriptionPlain") or j.get("description", "")
            salary = _extract_salary(j)
            out.append(JobPosting(
                source=self.name,
                company=company_token.replace("-", " ").title(),
                role=j.get("text", "").strip(),
                apply_link=j.get("hostedUrl", "") or j.get("applyUrl", ""),
                location=loc,
                work_type=(workplace.title() if workplace
                           else detect_work_type(loc, commitment, desc[:600])),
                salary=salary,
                description=desc,
                posted_at=parse_iso(j.get("createdAt")),
                job_id=str(j.get("id", "")),
                raw_status="open",
            ))
        return out


def _extract_salary(j) -> str:
    sr = j.get("salaryRange") or {}
    lo, hi, cur = sr.get("min"), sr.get("max"), sr.get("currency", "USD")
    if lo and hi:
        return f"${lo:,.0f} - ${hi:,.0f} {cur}".strip()
    return ""
