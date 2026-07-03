"""In-process login throttling — no external store.

Assumes a SINGLE worker process (production runs `gunicorn -w 1 --threads 8`), so
these counters live in memory and are shared across the worker's threads via a lock.
They reset on restart, which is acceptable: restarts aren't attacker-controlled, and a
sliding window naturally forgets old failures anyway. If you ever move to multiple
workers, back this with SQLite or Redis instead.
"""
import threading
import time

_LOCK = threading.Lock()
_FAILS: dict[str, list[float]] = {}   # key -> timestamps of recent failed attempts

WINDOW = 15 * 60        # seconds we remember a failure for
MAX_FAILS = 5           # failures per (ip, email) before that pair is locked
MAX_IP_FAILS = 20       # failures per ip (any email) — stops email-spraying from one host


def _prune(ts, now):
    return [t for t in ts if now - t < WINDOW]


def check(key, max_fails):
    """Return (allowed, retry_after_seconds). allowed=False once the key is locked."""
    now = time.time()
    with _LOCK:
        ts = _prune(_FAILS.get(key, []), now)
        if ts:
            _FAILS[key] = ts
        else:
            _FAILS.pop(key, None)          # bound memory: drop keys with no live failures
        if len(ts) >= max_fails:
            return False, max(1, int(ts[0] + WINDOW - now))
        return True, 0


def record_failure(key):
    now = time.time()
    with _LOCK:
        _FAILS.setdefault(key, []).append(now)


def clear(key):
    with _LOCK:
        _FAILS.pop(key, None)
