"""Provider registry + parallel fan-out fetch across all free ATS sources."""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .ashby import AshbyProvider
from .base import SearchTimeout
from .companies import REGISTRY, select_tokens
from .google_jobs import GoogleJobsProvider
from .greenhouse import GreenhouseProvider
from .jsearch import JSearchProvider
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


def fetch_all(max_per_provider=60, workers=16, timeout=12, progress=None,
              deadline=None, industries=None):
    """Fan out across every provider/company and return a flat list of JobPosting.

    `industries` (a set of industry tags from the candidate's resume) drives
    field-aware company selection so the limited per-provider budget lands on boards
    relevant to their field. `progress(done, total, msg)` is an optional callback for
    live status. `deadline` is a time.monotonic() value; exceeding it mid-scan raises
    SearchTimeout (queued board fetches are cancelled).
    """
    session = _make_session()
    providers = {name: cls(session, timeout=timeout)
                 for name, cls in PROVIDER_CLASSES.items()}

    tasks = []  # (provider_name, token)
    for name in PROVIDER_CLASSES:
        for token in select_tokens(name, industries, max_per_provider):
            tasks.append((name, token.strip()))

    total = len(tasks)
    results = []
    done = 0

    def _one(name, token):
        try:
            return providers[name].fetch(token)
        except Exception:
            return []

    # managed without a `with` block so we can shut down without waiting on the
    # queued futures when the deadline is hit
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {ex.submit(_one, name, token): (name, token)
                   for name, token in tasks if token}
        for fut in as_completed(futures):
            results.extend(fut.result())
            done += 1
            if progress:
                progress(done, total,
                         f"Scanned {done}/{total} boards — {len(results)} raw postings")
            if deadline and time.monotonic() > deadline:
                raise SearchTimeout("fetch phase exceeded time limit")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return results


def fetch_query_sources(query, location="", *, serpapi_key=None, pages=1,
                        timeout=15, progress=None, deadline=None):
    """Query-based providers (Google Jobs).

    Returns (results, status). status is human-readable so the UI can explain why
    Google Jobs did or didn't contribute (disabled / skipped / ok / error).
    """
    if not serpapi_key:
        return [], "disabled (no SERPAPI_KEY set)"
    if not query:
        return [], "skipped (no title or resume skills to query)"
    if deadline and time.monotonic() > deadline:
        return [], "skipped (time limit)"
    if progress:
        progress(0, 0, "Querying Google Jobs…")
    session = _make_session()
    gj = GoogleJobsProvider(session, serpapi_key, timeout=timeout, pages=pages)
    try:
        results = gj.search(query, location)
        status = f"ok — {len(results)} postings for '{query}'"
    except requests.exceptions.Timeout:
        results, status = [], "timed out — Google was slow this run (other sources still used)"
    except Exception as e:  # noqa: BLE001
        results, status = [], f"error: {e}"
    if progress:
        progress(0, 0, f"Google Jobs: {status}")
    return results, status


def fetch_jsearch_source(query, *, jsearch_key=None, pages=1, country=None,
                         remote_only=False, timeout=15, progress=None, deadline=None):
    """JSearch (LinkedIn/Indeed/ZipRecruiter…). Returns (results, status)."""
    if not jsearch_key:
        return [], "disabled (no JSEARCH_KEY set)"
    if not query:
        return [], "skipped (no title or resume skills to query)"
    if deadline and time.monotonic() > deadline:
        return [], "skipped (time limit)"
    if progress:
        progress(0, 0, "Querying JSearch (LinkedIn/Indeed/ZipRecruiter)…")
    session = _make_session()
    jp = JSearchProvider(session, jsearch_key, timeout=timeout, pages=pages)
    try:
        results = jp.search(query, country=country, remote_only=remote_only)
        status = f"ok — {len(results)} postings"
    except requests.exceptions.Timeout:
        results, status = [], "timed out — the provider was slow (other sources still used)"
    except Exception as e:  # noqa: BLE001
        results, status = [], f"error: {e}"
    if progress:
        progress(0, 0, f"JSearch: {status}")
    return results, status
