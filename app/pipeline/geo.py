"""Geographic / remote-eligibility detection and matching.

Public ATS feeds encode remote scope in the location string, e.g.
"Remote, US", "Remote (U.S.)", "Remote, Canada; Remote, US", "Remote, EMEA",
"Remote, Americas", "Remote, Japan". We parse those into canonical regions so a
"Remote USA" search no longer matches a "Remote Japan" posting.
"""
import re

# canonical country label -> regex of aliases (word-boundary anchored)
_COUNTRY = {
    "US": r"u\.?\s?s\.?\s?a?\.?|united states|stateside",
    "Canada": r"canada|canadian",
    "Mexico": r"mexico|méxico",
    "Brazil": r"brazil|brasil",
    "Argentina": r"argentina",
    "UK": r"u\.?k\.?|united kingdom|england|britain|scotland|wales",
    "Ireland": r"ireland|irish",
    "Germany": r"germany|deutschland",
    "France": r"france",
    "Spain": r"spain|españa",
    "Portugal": r"portugal",
    "Netherlands": r"netherlands|holland",
    "Belgium": r"belgium",
    "Austria": r"austria",
    "Switzerland": r"switzerland",
    "Italy": r"italy|italia",
    "Poland": r"poland",
    "Sweden": r"sweden",
    "Israel": r"israel",
    "UAE": r"uae|united arab emirates|abu dhabi|dubai",
    "India": r"india",
    "Singapore": r"singapore",
    "Japan": r"japan",
    "China": r"china",
    "Korea": r"korea",
    "Philippines": r"philippines",
    "Indonesia": r"indonesia",
    "Australia": r"australia",
    "New Zealand": r"new zealand",
}

# broad region groups -> member canonical countries (for scope containment)
_GROUPS = {
    "Worldwide": set(_COUNTRY),  # matches anything
    "North America": {"US", "Canada", "Mexico"},
    "Americas": {"US", "Canada", "Mexico", "Brazil", "Argentina"},
    "LATAM": {"Mexico", "Brazil", "Argentina"},
    "Europe": {"UK", "Ireland", "Germany", "France", "Spain", "Portugal",
               "Netherlands", "Belgium", "Austria", "Switzerland", "Italy",
               "Poland", "Sweden"},
    "EMEA": {"UK", "Ireland", "Germany", "France", "Spain", "Portugal",
             "Netherlands", "Belgium", "Austria", "Switzerland", "Italy",
             "Poland", "Sweden", "Israel", "UAE"},
    "APAC": {"India", "Singapore", "Japan", "China", "Korea", "Philippines",
             "Indonesia", "Australia", "New Zealand"},
}

_GROUP_ALIAS = {
    "Worldwide": r"worldwide|global(?:ly)?|anywhere|international|any location|"
                 r"work from anywhere",
    "North America": r"north america|n\.? ?america|namer",
    "Americas": r"americas",
    "LATAM": r"latam|latin america",
    "Europe": r"europe|european|emea-?europe|europe-?(north|central|west|east)",
    "EMEA": r"emea",
    "APAC": r"apac|asia[- ]?pacific|asia",
}

_US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
    "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
    "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}

_COUNTRY_RE = {c: re.compile(r"\b(?:" + pat + r")\b", re.I) for c, pat in _COUNTRY.items()}
_GROUP_RE = {g: re.compile(r"\b(?:" + pat + r")\b", re.I) for g, pat in _GROUP_ALIAS.items()}


def detect_regions(text: str):
    """Return (countries:set, groups:set) named in `text`."""
    if not text:
        return set(), set()
    countries = {c for c, rx in _COUNTRY_RE.items() if rx.search(text)}
    groups = {g for g, rx in _GROUP_RE.items() if rx.search(text)}
    return countries, groups


def desired_regions(location_query: str):
    """Parse the user's location box into a set of canonical countries.

    Empty set => no country constraint (e.g. they typed just "Remote" or a city).
    """
    if not location_query:
        return set()
    countries, _ = detect_regions(location_query)
    # US state abbreviations imply the US
    toks = re.findall(r"[a-zA-Z]+", location_query.lower())
    if any(t in _US_STATES for t in toks):
        countries.add("US")
    return countries


def _in_scope(country: str, countries: set, groups: set) -> bool:
    if country in countries:
        return True
    for g in groups:
        if country in _GROUPS.get(g, set()):
            return True
    return False


def is_remote(job) -> bool:
    return "remote" in (job.work_type or "").lower() or \
           "remote" in (job.location or "").lower()


def remote_scope_label(job) -> str:
    """Human-readable confirmation of where the posting allows remote work."""
    if not is_remote(job):
        return ""
    countries, groups = detect_regions(job.location)
    if not countries and not groups:
        countries, groups = detect_regions(job.description[:600])
    if "Worldwide" in groups:
        return "Remote — Worldwide"
    labels = sorted(groups) + sorted(countries)
    if labels:
        return "Remote — " + ", ".join(labels)
    return "Remote — region not specified"


def assess(desired: set, job):
    """Return (verdict, fit_score 0..1). verdict in match/ok/unknown/conflict.

    'conflict' => the posting explicitly allows only regions disjoint from the
    user's desired country and should be dropped.
    """
    if not desired:
        return "ok", 1.0
    countries, groups = detect_regions(job.location)
    if not countries and not groups:
        countries, groups = detect_regions(job.description[:600])
    if not countries and not groups:
        return "unknown", 0.7  # remote but no stated geo — keep, mild
    if "Worldwide" in groups:
        return "match", 1.0
    if any(_in_scope(d, countries, groups) for d in desired):
        return "match", 1.0
    return "conflict", 0.12
