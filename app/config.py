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


def _int_env(name, default):
    """Parse an int env var, falling back to default on missing/blank/bad values
    so a mistyped setting can never crash the app at startup."""
    raw = os.environ.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except (ValueError, TypeError):
        return default


_HTTPS = os.environ.get("ROLEQUILL_HTTPS", "0") == "1"
# Optional cookie domain — set to e.g. ".rolequill.com" so auth cookies (session +
# the 2FA "remember this device" cookie) span BOTH the apex and www hosts. A host-only
# cookie set on rolequill.com is NOT sent to www.rolequill.com, which silently breaks
# "remember this device" for users who bounce between the two. Leave unset = host-only.
_COOKIE_DOMAIN = (os.environ.get("ROLEQUILL_COOKIE_DOMAIN") or "").strip() or None


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
    SESSION_COOKIE_DOMAIN = _COOKIE_DOMAIN   # None = host-only (Flask default)
    COOKIE_DOMAIN = _COOKIE_DOMAIN           # reused for the 2FA remember-device cookie
    PREFERRED_URL_SCHEME = "https" if _HTTPS else "http"
    # set when running behind a reverse proxy / Cloudflare so url_for builds https
    BEHIND_PROXY = os.environ.get("ROLEQUILL_BEHIND_PROXY", "0") == "1"
    UPLOAD_DIR = str(UPLOAD_DIR)
    EXPORT_DIR = str(EXPORT_DIR)
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB resume cap
    ALLOWED_RESUME_EXT = {".pdf", ".docx", ".txt"}
    # How many companies to fan out to per provider per search (keeps runtime sane)
    MAX_COMPANIES_PER_PROVIDER = _int_env("JOBSEARCH_MAX_COMPANIES", 60)
    FETCH_WORKERS = _int_env("JOBSEARCH_WORKERS", 16)
    FETCH_TIMEOUT = 12  # seconds per per-company ATS call (many, fanned out in parallel)
    # Query aggregators (SerpApi Google Jobs, JSearch) are a single live search that can
    # legitimately take longer than one ATS board — give them a bigger ceiling so they
    # aren't cut off (SerpApi's google_jobs engine often takes 10-20s).
    QUERY_TIMEOUT = _int_env("ROLEQUILL_QUERY_TIMEOUT", 30)
    # overall wall-clock limit per search; exceeding it cancels + refunds the credit
    SEARCH_TIME_LIMIT = _int_env("ROLEQUILL_SEARCH_TIMEOUT", 120)

    # Google Jobs via SerpApi (optional; disabled until a key is set).
    # Accept either common env var name.
    SERPAPI_KEY = os.environ.get("SERPAPI_KEY") or os.environ.get("SERPAPI_API_KEY")
    GOOGLE_JOBS_PAGES = _int_env("ROLEQUILL_GOOGLE_PAGES", 1)

    # JSearch via RapidAPI — LinkedIn/Indeed/ZipRecruiter (optional; off until keyed)
    JSEARCH_KEY = os.environ.get("JSEARCH_KEY") or os.environ.get("RAPIDAPI_KEY")
    JSEARCH_PAGES = _int_env("ROLEQUILL_JSEARCH_PAGES", 1)

    # Owner-only admin dashboard — the account with this email sees /admin
    ADMIN_EMAIL = (os.environ.get("ROLEQUILL_ADMIN_EMAIL") or "").strip().lower()

    # Transactional email via Resend (optional; password reset is disabled until set).
    # Verify rolequill.com in Resend and add its SPF/DKIM records in Cloudflare so mail
    # doesn't land in spam. RESEND_FROM must use a verified domain.
    # Strip whitespace/surrounding quotes so a dashboard value pasted as
    # "RoleQuill <noreply@rolequill.com>" (quotes included) doesn't 422 at Resend.
    RESEND_API_KEY = (os.environ.get("RESEND_API_KEY") or "").strip() or None
    RESEND_FROM = ((os.environ.get("RESEND_FROM") or "").strip().strip('"').strip("'").strip()
                   or "RoleQuill <noreply@rolequill.com>")

    # AI features via the Anthropic Messages API (optional; all AI degrades to the
    # non-AI behavior until a key is set). Raw HTTPS through `requests` — no SDK, no
    # new deps (matches mailer.py), so the 512MB Starter footprint stays flat. Model
    # split is cost-optimized: cheap Haiku for bulk/per-job work, Sonnet for the
    # one-shot resume analysis. Override either via env to change models in one place.
    ANTHROPIC_API_KEY = (os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None
    AI_MODEL_BULK = (os.environ.get("ROLEQUILL_AI_MODEL_BULK") or "").strip() or "claude-haiku-4-5"
    AI_MODEL_ANALYSIS = (os.environ.get("ROLEQUILL_AI_MODEL_ANALYSIS") or "").strip() or "claude-sonnet-5"

    # Payments / credits — 'stub' (instant test fulfillment) or 'stripe' (live)
    PAYMENTS_MODE = os.environ.get("ROLEQUILL_PAYMENTS_MODE", "stub")
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
