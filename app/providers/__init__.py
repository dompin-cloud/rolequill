"""Provider registry + parallel fan-out fetch across all free ATS sources."""
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .ashby import AshbyProvider
from .companies import REGISTRY
from .google_jobs import GoogleJobsProvider
from .greenhouse import GreenhouseProvider
from .lever import LeverProvider

PROVIDER_CLASSES = {
    "Greenhouse": GreenhouseProvider,
    "Lever": LeverProvider,
    "Ashby": AshbyProvider,
}

USER_AGENT = "JobSearchService/1.0 (+https://localhost) python-requests"


def _make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def fetch_all(max_per_provider=60, workers=16, timeout=12, progress=None):
    """Fan out across every provider/company and return a flat list of JobPosting.

    `progress(done, total, msg)` is an optional callback for live status updates.
    """
    session = _make_session()
    providers = {name: cls(session, timeout=timeout)
                 for name, cls in PROVIDER_CLASSES.items()}

    tasks = []  # (provider_name, token)
    for name, tokens in REGISTRY.items():
        for token in tokens[:max_per_provider]:
            tasks.append((name, token.strip()))

    total = len(tasks)
    results = []
    done = 0

    def _one(name, token):
        try:
            return providers[name].fetch(token)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_one, name, token): (name, token)
                   for name, token in tasks if token}
        for fut in as_completed(futures):
            name, token = futures[fut]
            jobs = fut.result()
            results.extend(jobs)
            done += 1
            if progress:
                progress(done, total,
                         f"Scanned {done}/{total} boards — {len(results)} raw postings")
    return results


def fetch_query_sources(query, location="", *, serpapi_key=None, pages=1,
                        timeout=15, progress=None):
    """Query-based providers (Google Jobs).

    Returns (results, status). status is human-readable so the UI can explain why
    Google Jobs did or didn't contribute (disabled / skipped / ok / error).
    """
    if not serpapi_key:
        return [], "disabled (no SERPAPI_KEY set)"
    if not query:
        return [], "skipped (no title or resume skills to query)"
    if progress:
        progress(0, 0, "Querying Google Jobs…")
    session = _make_session()
    gj = GoogleJobsProvider(session, serpapi_key, timeout=timeout, pages=pages)
    try:
        results = gj.search(query, location)
        status = f"ok — {len(results)} postings for '{query}'"
    except Exception as e:  # noqa: BLE001
        results, status = [], f"error: {e}"
    if progress:
        progress(0, 0, f"Google Jobs: {status}")
    return results, status
