# RoleQuill — Session Handoff / Context

Paste this into a new session (or just reference it) to continue without re-deriving
everything. Last updated: 2026-07-03.

## What RoleQuill is
A multi-user **Flask** web service (Python; local 3.14, Render 3.13) that runs an
automated job search from a resume + criteria, filters junk, scores matches, and
delivers an Excel workbook + in-app report. **Live in production** with paid Stripe.

- **Repo:** github.com/dompin-cloud/rolequill (branch `main`, auto-deploys to Render)
- **Host:** Render (paid Starter, 512 MB) — service NOT fully Blueprint-synced, so
  Start Command / disk / env vars were set **manually in the Render dashboard**.
- **Domain:** rolequill.com via **Cloudflare** (DNS + TLS Full-strict)
- **Server cmd:** `gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:$PORT wsgi:app`
- **Persistent disk:** mounted at `/var/data`; `ROLEQUILL_DATA_DIR=/var/data` so the
  SQLite db + resumes + exports survive redeploys. **Required** — without it, data resets.

## Architecture / pipeline
`app/` package, factory in `app/__init__.py`. Search flow (`app/pipeline/__init__.py`):
fetch → **cross-source dedupe** (normalized company+role, prefers direct-ATS link) →
classify (ghost/scam/stale) → title gate → **work-type hard gate** → geo gate →
score → rank (top 40). Runs in a background thread (`app/search_runner.py`) with a
120s wall-clock limit.

### Providers (`app/providers/`)
- **Greenhouse / Lever / Ashby** — free public per-company ATS APIs; company tokens
  in `companies.py` (capped by `JOBSEARCH_MAX_COMPANIES`).
- **Google Jobs** — SerpApi (`google_jobs.py`), key `SERPAPI_KEY`, endpoint returns
  LinkedIn/Indeed/etc. via Google.
- **JSearch** — RapidAPI (`jsearch.py`), key `JSEARCH_KEY`. **v5 endpoint is
  `/search-v2`**, results nested under `data.jobs`. Adds LinkedIn/Indeed/ZipRecruiter.
- No direct LinkedIn/Indeed/ZipRecruiter scraper (ToS). Use aggregators only.

### Scoring (`app/pipeline/`)
`scoring.py` (match + ATS score), `keywords.py` (skill taxonomy, single-pass regex),
`profile.py` (resume Core Skills/Education/Professional Development), `geo.py`
(remote region matching), `lang.py` (language requirements), `filters.py` (junk).
Title is **optional** (blank = skills-first).

### Outputs
- **Excel** (`excel_export.py`) — 6 sheets matching the user's original template.
- **In-app full report** — `/search/<id>/report` renders all 6 sections from a stored
  `report_json` (`report.py`). Built at search time.

## Features live
- Accounts (register/login, hashed pw), resume upload (PDF/DOCX/TXT) + parsing
- **Credits** (`credits.py`): freemium + pay-as-you-go. 3 free on signup, +1/week
  (cap 2), purchased never expire. Auto-refund on: 0 results, 120s timeout, and
  restart-orphaned searches (startup `reconcile_orphans`). Packs: Starter 5/$5,
  Plus 15/$12, Pro 40/$25 (`payments.py`, inline price_data — Stripe products optional).
- **Stripe** LIVE (runicmenagerie account). Webhook `POST /credits/webhook`, verifies
  signature then reads **raw JSON** (Stripe lib objects aren't dict-like). Idempotent
  via `stripe_events` table. Snapshot payload destination named "RoleQuill".
- **Application tracker** (`/applications`): Track button on results → status
  (Applied→Replied→Interview→Offer/Rejected) → **reply rate** metric.
- **Admin** (`/admin`): owner-only via `ROLEQUILL_ADMIN_EMAIL` (404 for others). Shows
  users, applications, reply rate, matches. Uses **initials, not emails** (PII) — the
  admin UI never selects/renders email or resume text (backs the privacy-policy claim).
- **Search tips** on dashboard + empty-results state. Source ✓/✗ indicators on dashboard.
- **Privacy & data control** (`app/main.py` + `templates/legal/`, `templates/account.html`):
  - Public `/privacy` + `/terms` pages (footer links), operated by **Dominic Pinoteau**,
    governing law **Wisconsin, USA**, contact dompin@runicprinting.com. Commitment-based
    stance: honest that the owner retains raw DB access, used only to run/debug/secure.
  - `/account` self-service page: **data export** (`/account/export` → JSON of profile,
    resumes incl. extracted text, searches, applications, credit ledger; password hash
    excluded) and **account deletion** (`/account/delete`, POST, must type email to
    confirm → deletes user row [children cascade] **and** unlinks resume/export files
    from `/var/data`). Right-of-access + right-of-erasure.
  - **Resume delete** on dashboard (`/resume/<id>/delete`) removes row + stored file
    (`searches.resume_id` is `ON DELETE SET NULL`, so past searches survive).
  - **Signup consent**: required Terms/Privacy checkbox, enforced server-side in `auth.py`.
  - `_safe_unlink()` helper in `main.py` deletes disk files best-effort (never raises).

## Env vars (all set in Render dashboard; local dev uses a gitignored `.env`)
`ROLEQUILL_SECRET`, `ROLEQUILL_DEBUG=0`, `ROLEQUILL_HTTPS=1`,
`ROLEQUILL_BEHIND_PROXY=1`, `ROLEQUILL_DATA_DIR=/var/data`,
`ROLEQUILL_PAYMENTS_MODE=stripe`, `STRIPE_SECRET_KEY` (live), `STRIPE_PUBLISHABLE_KEY`
(live), `STRIPE_WEBHOOK_SECRET`, `SERPAPI_KEY`, `JSEARCH_KEY`,
`JOBSEARCH_MAX_COMPANIES` (currently 25), `JOBSEARCH_WORKERS=6`, `ROLEQUILL_ADMIN_EMAIL`,
optional `ROLEQUILL_GOOGLE_PAGES`/`ROLEQUILL_JSEARCH_PAGES` (default 1).
Numeric env vars are parsed with `_int_env` (blank/bad value → default, never crashes).

## Hard-won gotchas
- `.env` is NOT deployed (gitignored); Render uses **dashboard env vars**.
- **A blank/non-number numeric env var crashed boot** ("status 1"); fixed via `_int_env`.
- Dependencies are **pinned** in `requirements.txt`; `.python-version=3.13` (minor only).
- Render keeps the **last good deploy live** when a new deploy fails — no downtime.
- Rapid successive pushes → Render cancels in-progress deploys (shows as "failed").
- Startup prints diagnostics: `.env` found?, DB path, Google/JSearch on?, Payments mode.
- Run locally: `python run.py` → http://127.0.0.1:5000 (has a port-in-use guard).

## Open threads / next steps discussed
- **Coverage:** search now scans 75 ATS boards (25×3) due to `JOBSEARCH_MAX_COMPANIES=25`
  (set during the OOM fight). User wants more → raise it in Render (try 60) and watch
  for OOM, or upgrade Render to Standard (2 GB, ~$25/mo). Can also expand company token
  lists in `companies.py`.
- **Security housekeeping:** a Stripe `rk_live_` key and the JSearch key were pasted in
  chat during setup — optional to rotate them.
- **Security housekeeping (next):** no **login rate-limiting** yet (brute-force gap);
  still worth rotating the keys pasted in chat during setup.
- **Legal review:** privacy/terms are self-drafted templates — have a professional
  review if operating at scale or serving EU/UK users.
- **Future ideas:** password reset flow; SQLite→Postgres + task queue at scale;
  email-forward assist for auto reply-tracking; per-source reply-rate on admin.

## Recent commit trail (newest first)
privacy/data-control (policy+terms, account export/delete, resume delete, consent) →
env-parse crash fix → pin deps + loosen python → JSearch indicator → JSearch v5
/search-v2 → JSearch provider → cross-source dedupe → admin initials (PII) → admin
simplify → tips + full report → app tracker → admin dashboard → search timeout refund →
orphan reconcile.
