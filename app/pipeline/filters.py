"""Quality filters: drop ghost posts, scams, and stale/closed positions.

A key structural advantage: the free ATS feeds (Greenhouse/Lever/Ashby) only
serve *currently open* requisitions, so genuinely closed jobs are excluded at the
source. These heuristics catch the residue: evergreen "ghost" reqs, stale
listings, and scam signals in the description.
"""
import re

# Titles that signal an evergreen / talent-pipeline "ghost" post (no real opening)
GHOST_TITLE_RE = re.compile(
    r"\b(general application|talent (community|pool|network)|future opportunities|"
    r"evergreen|pipeline|speculative|don't see (a|your) role|expression of interest|"
    r"introduce yourself|join our talent)\b", re.I)

SCAM_RE = re.compile(
    r"\b(wire transfer|western union|moneygram|registration fee|processing fee|"
    r"upfront (payment|fee)|send money|gift card|bitcoin payment|"
    r"interview (via|on) (telegram|whatsapp|google hangouts|signal)|"
    r"text me on (telegram|whatsapp|signal)|personal email .* to apply|"
    r"no experience necessary.*\$\d{3,}/?(day|hour))\b", re.I)

# Stale: ATS still lists it but it's been open a long time (likely backfilled/ghost)
STALE_AGE_DAYS = 75
# Ghosty: open suspiciously long with a generic title
SOFT_STALE_AGE_DAYS = 45


def classify(job) -> tuple[bool, list[str]]:
    """Return (keep, reasons). If keep is False, `reasons` says why it was dropped."""
    reasons = []
    title = job.role or ""
    desc = job.description or ""

    if not job.apply_link or not job.apply_link.startswith("http"):
        return False, ["no valid application link"]

    if GHOST_TITLE_RE.search(title):
        return False, ["ghost: evergreen / talent-pipeline post"]

    if SCAM_RE.search(desc) or SCAM_RE.search(title):
        return False, ["scam signals in description"]

    age = job.age_days
    if age is not None:
        if age > STALE_AGE_DAYS:
            return False, [f"stale: open {int(age)} days (likely closed/backfilled)"]
        if age > SOFT_STALE_AGE_DAYS and GHOST_TITLE_RE.search(desc[:400]):
            return False, [f"ghost: generic + open {int(age)} days"]

    # Kept — attach soft advisory flags (do not drop)
    if age is not None and age > SOFT_STALE_AGE_DAYS:
        reasons.append(f"aging: open {int(age)} days")
    return True, reasons
