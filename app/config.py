"""Application configuration."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Where the SQLite db, uploaded resumes, and Excel exports live. Point this at a
# PERSISTENT DISK in production (e.g. ROLEQUILL_DATA_DIR=/var/data on Render) so
# accounts/credits survive redeploys. Defaults to ./data for local dev.
DATA_DIR = Path(os.environ.get("ROLEQUILL_DATA_DIR") or (BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "resumes"
EXPORT_DIR = DATA_DIR / "exports"

for _d in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv(path):
    """Tiny .env loader (no dependency). A NON-EMPTY real env var takes precedence.

    Lets keys (SERPAPI_KEY, Stripe, etc.) live in a file on disk so the app picks
    them up no matter which terminal/launcher starts it. Format: KEY=value per line,
    '#' comments and blank lines ignored, optional surrounding quotes stripped.
    Returns True if the file existed. Handles UTF-8 and UTF-16 (PowerShell default).
    """
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-16")  # PowerShell '>' / Out-File default
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # fill if the env var is missing OR empty (don't let a blank OS var win)
        if key and not os.environ.get(key):
            os.environ[key] = val
    return True


DOTENV_PATH = BASE_DIR / ".env"
DOTENV_LOADED = _load_dotenv(DOTENV_PATH)


_HTTPS = os.environ.get("ROLEQUILL_HTTPS", "0") == "1"


class Config:
    SECRET_KEY = (os.environ.get("ROLEQUILL_SECRET")
                  or os.environ.get("JOBSEARCH_SECRET")
                  or "dev-change-me-in-production")
    DEBUG = os.environ.get("ROLEQUILL_DEBUG", "0") == "1"
    DATABASE = str(DATA_DIR / "jobsearch.db")

    # security — enable HTTPS-only cookies in production (set ROLEQUILL_HTTPS=1)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _HTTPS
    PREFERRED_URL_SCHEME = "https" if _HTTPS else "http"
    # set when running behind a reverse proxy / Cloudflare so url_for builds https
    BEHIND_PROXY = os.environ.get("ROLEQUILL_BEHIND_PROXY", "0") == "1"
    UPLOAD_DIR = str(UPLOAD_DIR)
    EXPORT_DIR = str(EXPORT_DIR)
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB resume cap
    ALLOWED_RESUME_EXT = {".pdf", ".docx", ".txt"}
    # How many companies to fan out to per provider per search (keeps runtime sane)
    MAX_COMPANIES_PER_PROVIDER = int(os.environ.get("JOBSEARCH_MAX_COMPANIES", "60"))
    FETCH_WORKERS = int(os.environ.get("JOBSEARCH_WORKERS", "16"))
    FETCH_TIMEOUT = 12  # seconds per HTTP call

    # Google Jobs via SerpApi (optional; disabled until a key is set).
    # Accept either common env var name.
    SERPAPI_KEY = os.environ.get("SERPAPI_KEY") or os.environ.get("SERPAPI_API_KEY")
    GOOGLE_JOBS_PAGES = int(os.environ.get("ROLEQUILL_GOOGLE_PAGES", "1"))

    # Payments / credits — 'stub' (instant test fulfillment) or 'stripe' (live)
    PAYMENTS_MODE = os.environ.get("ROLEQUILL_PAYMENTS_MODE", "stub")
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
