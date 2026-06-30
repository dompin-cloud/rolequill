"""Greenhouse public job board API (free, no key).

Endpoint: https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
"""
from .base import (JobPosting, Provider, detect_work_type, format_pay,
                   html_to_text, parse_iso)

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


class GreenhouseProvider(Provider):
    name = "Greenhouse"

    def fetch(self, company_token: str) -> list[JobPosting]:
        url = API.format(token=company_token)
        resp = self.session.get(url, timeout=self.timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        out = []
        for j in data.get("jobs", []):
            content = html_to_text(j.get("content", ""))
            loc = (j.get("location") or {}).get("name", "") or ""
            offices = ", ".join(
                o.get("name", "") for o in (j.get("offices") or []) if o.get("name")
            )
            salary = _extract_salary(j)
            out.append(JobPosting(
                source=self.name,
                company=company_token.replace("-", " ").title(),
                role=j.get("title", "").strip(),
                apply_link=j.get("absolute_url", ""),
                location=loc or offices,
                work_type=detect_work_type(loc, offices, content[:600]),
                salary=salary,
                description=content,
                posted_at=parse_iso(j.get("updated_at") or j.get("first_published")),
                job_id=str(j.get("id", "")),
                raw_status="open",
            ))
        return out


def _extract_salary(j) -> str:
    # Greenhouse exposes pay in pay_input_ranges on some boards, else in metadata.
    rng = j.get("pay_input_ranges") or []
    if rng:
        r = rng[0]
        lo, hi = r.get("min_cents"), r.get("max_cents")
        cur = r.get("currency_type", "USD")
        if lo and hi:
            return f"${lo/100:,.0f} - ${hi/100:,.0f} {cur}".strip()
    for m in j.get("metadata") or []:
        name = (m.get("name") or "").lower()
        if "salary" in name or "compensation" in name or "pay" in name:
            v = m.get("value")
            if v:
                return format_pay(v)
    return ""
