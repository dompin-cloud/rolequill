# RoleQuill — Session Handoff / Context

Paste this into a new session (or just reference it) to continue without re-deriving
everything. Last updated: 2026-07-04 (added opt-in email 2FA).

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
- **Login rate-limiting** (`app/ratelimit.py`): in-process sliding window, shared across
  the worker's threads via a lock. Locks a (ip,email) pair after 5 failures and an ip
  after 20 (spray) for 15 min. **Assumes `gunicorn -w 1`** (counters are in-memory, reset
  on restart); move to SQLite/Redis if going multi-worker. Uses `request.remote_addr`
  (accurate because ProxyFix is on behind Cloudflare).
- **Password reset** (`auth.py` `/forgot` + `/reset/<token>`): single-use tokens in the
  `password_resets` table (stores SHA-256 of the token; raw token only in the email link),
  1-hour expiry, invalidated on use. No account enumeration (identical response whether or
  not the email exists). Reset requests are rate-limited (5/IP). Email sent via
  **Resend** (`app/mailer.py`, HTTPS API over `requests` — no new deps); body in
  `templates/email/reset.html`. Disabled gracefully if `RESEND_API_KEY` unset.
- **Two-factor auth (email OTP, opt-in)** (`auth.py`): per-user `users.twofa_email`
  flag, enabled from `/account`. On login from an untrusted device we email a 6-digit
  code (`login_codes` table, SHA-256 of the code, single-use, 10-min TTL, 5 wrong-guess
  cap) and hold the session as `pending_2fa_user` until `/auth/verify` succeeds. Codes
  and resends are rate-limited via `ratelimit.py`. Enrollment (`/auth/2fa/enable` →
  `/auth/2fa/confirm`) sends a code first so a dead inbox can't self-lockout; disable
  (`/auth/2fa/disable`) requires the current password. **"Trust this device"** (30 days)
  = a random token whose SHA-256 is stored in `trusted_devices`, raw token in the `rq_td`
  cookie; skips the code on that browser. **Sliding renewal**: each trusted login extends
  the expiry + reissues the cookie, so an active device stays trusted for a rolling 30 days.
  The cookie honors `COOKIE_DOMAIN` — **set `ROLEQUILL_COOKIE_DOMAIN=.rolequill.com` in Render**
  so it (and the session cookie) span apex + www; a host-only cookie set on `rolequill.com`
  is NOT sent to `www.rolequill.com`, which was the likely cause of "remember me" not sticking.
  Password reset revokes trusted devices + pending codes. **Recovery is email-bound** (OTP +
  reset both go to the inbox) — a user who loses
  email access is locked out; escape hatch is `UPDATE users SET twofa_email=0 WHERE email=?`
  in the DB. Needs `RESEND_API_KEY` set (the Enable button hides itself if email is off).
  Backup one-time recovery codes are a sensible phase-2 add.
- **CSRF protection** (`app/csrf.py`, stdlib-only, no Flask-WTF): per-session token, validated
  in a `before_request` hook (constant-time compare), rendered into every POST form via the
  `{{ csrf_token() }}` helper. Stripe webhook is exempt (session-less, signature-verified).
  Defense-in-depth atop the SameSite=Lax session cookie.
- **Credits** (`credits.py`): freemium + pay-as-you-go. 3 free on signup, +1/week
  (cap 2), purchased never expire. Auto-refund on: 0 results, 120s timeout, and
  restart-orphaned searches (startup `reconcile_orphans`). Packs: Starter 5/$5,
  Plus 15/$12, Pro 40/$25 (`payments.py`, inline price_data — Stripe products optional).
- **Stripe** LIVE (runicmenagerie account). Webhook `POST /credits/webhook`, verifies
  signature then reads **raw JSON** (Stripe lib objects aren't dict-like). Idempotent
  via `stripe_events` table. Snapshot payload destination named "RoleQuill".
- **Application tracker** (`/applications`): ＋Track button on results OR **manual entry**
  (`/applications/add-manual`, backdatable) → status (Applied→Replied→Interview→
  Offer/Rejected) → **reply rate** metric. Collapsible tracking-tips + add-form boxes.
  Linkless manual entries store apply_link as NULL (UNIQUE(user_id, apply_link) index).
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
optional `ROLEQUILL_COOKIE_DOMAIN=.rolequill.com` (span apex+www for session + 2FA cookies),
`ROLEQUILL_PAYMENTS_MODE=stripe`, `STRIPE_SECRET_KEY` (live), `STRIPE_PUBLISHABLE_KEY`
(live), `STRIPE_WEBHOOK_SECRET`, `SERPAPI_KEY`, `JSEARCH_KEY`,
`JOBSEARCH_MAX_COMPANIES` (currently 25), `JOBSEARCH_WORKERS=6`, `ROLEQUILL_ADMIN_EMAIL`,
optional `ROLEQUILL_GOOGLE_PAGES`/`ROLEQUILL_JSEARCH_PAGES` (default 1).
**Email (password reset):** `RESEND_API_KEY`, optional `RESEND_FROM` (default
`RoleQuill <noreply@rolequill.com>`; must use a Resend-verified domain).
**Timeouts:** `FETCH_TIMEOUT`=12 (per ATS board), `ROLEQUILL_QUERY_TIMEOUT`=30 (SerpApi
Google Jobs + JSearch — they're a single slow live search; raise if Google Jobs "timed out").
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
- **Email: DONE & live** — Resend key set in Render, rolequill.com verified with
  SPF/DKIM in Cloudflare; password reset delivers end-to-end. (Gotcha fixed: a
  dashboard `RESEND_FROM` value pasted with surrounding quotes 422'd at Resend →
  config now strips quotes/whitespace.)
- **Security housekeeping:** a Stripe `rk_live_` key and the JSearch key were pasted in
  chat during earlier setup — user is rotating them.
- **Legal review:** privacy/terms are self-drafted templates (operator Dominic
  Pinoteau, Wisconsin law) — have a professional review if operating at scale or
  serving EU/UK users.
- **Future ideas:** surface application notes/location as columns (captured + exported,
  not yet displayed); SQLite→Postgres + task queue at scale; email-forward assist for
  auto reply-tracking; per-source reply-rate on admin.
- **Industry generalization (make it work for ANY field, not just tech):** the ATS
  roster (`companies.py`, 137 tech companies) and skill taxonomy (`keywords.py`, all
  software/AI/SaaS) hardcode a tech universe; the resume only personalizes ranking.
  Plan (owner chose to generalize): **B1 DONE** — aggregator query (Google/JSearch) is
  now resume-driven via `profile.detect_roles`/`search_query` (field-agnostic occupation
  detection), so a nurse/accountant/chef resume searches its actual field. **B2 DONE** —
  resume-agnostic scoring (`scoring._role_match`): a field-agnostic role-match path
  (does the resume's occupation head-noun appear in the JD title/body?) runs alongside
  the tech-taxonomy path, and `skill_component = max(tax_path, role_path)` so tech scoring
  never regresses while non-tech jobs score on role + resume-term overlap. A role match
  credits `relevance_hits` so field-relevant non-tech jobs clear the skills-first gate;
  cross-field jobs (nurse resume vs tech role) match neither path and stay filtered. Also
  fixed the "Title closely matches" boilerplate (only shows when a title was actually
  typed). **B3 (next)**: industry-segmented company rosters for real non-tech ATS
  coverage (large content effort) — until then non-tech jobs come only from Google/JSearch,
  not the ATS boards.

## Global expansion (future planning — not started)
Making RoleQuill viable for users outside the US, ordered by what actually blocks it.
Two hard gates, then polish. Nothing here is built yet.
- **GATE 1 — Job-data coverage (the product gate).** Discovery is US-biased today:
  `companies.py` tokens are mostly US tech employers; `google_jobs.py` only does
  `f"{query} {location}"` (does NOT pass SerpApi `gl`/`hl` locale params); `jsearch.py`
  accepts a `country` param but the UI never collects the user's country to feed it.
  Result: thin/US-shaped results abroad. **Fix:** collect user country/region, thread it
  to `jsearch` (`country`) and `google_jobs` (`gl`/`hl`/`location`), expand `companies.py`
  with regional employers, handle non-English postings in `lang.py`/`keywords.py`.
  Highest leverage, pure code — the recommended first step whenever this starts.
- **GATE 2 — Legal / data protection (the compliance gate).** Serving EU/UK users triggers
  GDPR/UK GDPR (also PIPEDA, LGPD, etc.); resumes = personal data. Foundation is decent
  (`/account/export` + `/account/delete` = access+erasure, signup consent). Missing:
  professionally reviewed privacy policy/DPA (current one is self-drafted, Wisconsin law),
  cookie/consent basis, lawful-basis docs, possibly an EU representative. Real gate for EU.
- **Payments & tax (not a launch blocker).** `payments.py` hardcodes `currency:"usd"`;
  intl users can pay USD early. The hard part is tax on digital goods (EU VAT, UK VAT, GST).
  Pragmatic solo-operator move: switch to a **Merchant-of-Record** (Paddle / Lemon Squeezy)
  so they're seller-of-record and file global tax for you, instead of raw Stripe.
- **Localization / i18n (defer).** Templates are English-only, no framework. English-first
  global launch is fine for most markets; add Flask-Babel + translations + currency/date
  formatting (+ RTL) only once traction justifies it.
- **Infra ceiling (scale, not launch).** Single Render worker, single US region, SQLite +
  in-memory rate-limiting (`-w 1` assumption). Global = latency + can't add workers/regions
  without Postgres + Redis-backed throttling. The wall you hit *with* success.
- **Sanctions:** can't serve OFAC-embargoed countries — a signup-country block is cheap
  insurance.
- **2FA is already global-friendly:** email-OTP works anywhere email works, no per-country
  phone deliverability/cost. (SMS 2FA is where global would've gotten expensive — avoided.)
- **Recommended MVP sequence:** (a) locale-aware search, (b) Merchant-of-Record billing,
  (c) professional privacy/terms + consent banner. Defer i18n + multi-region.

## Recent commit trail (newest first)
industry generalization B2: resume-agnostic scoring (scoring._role_match — field
role-match path via max(tax_path, role_path)) so non-tech jobs score + clear the gate;
also fixes the "Title closely matches" boilerplate →
industry generalization B1: resume-driven aggregator query (profile.detect_roles +
search_query) so non-tech resumes search their real field via Google Jobs/JSearch →
security/quality pass: CSRF tokens on all POSTs (app/csrf.py) → 2FA remember-device
sliding renewal + configurable cookie domain (apex/www) → same-host redirect guard +
charge-before-insert ordering fix → result quality: per-company cap (3) + IDF skill
weighting (two-pass scoring so ubiquitous skills stop inflating off-target roles) →
opt-in email 2FA (one-time login codes: login_codes + trusted_devices tables,
users.twofa_email flag, /auth/verify + enroll/disable, "trust this device") →
manual application entry (Applications page) → query-aggregator timeout split
(QUERY_TIMEOUT=30 so SerpApi isn't cut off at 12s) + tracking tips moved atop results →
application-tracking tips (search results + Applications pages) →
RESEND_FROM/key hardening + email startup diagnostic →
login rate-limit + password reset (Resend email, single-use tokens) →
privacy/data-control (policy+terms, account export/delete, resume delete, consent) →
env-parse crash fix → pin deps + loosen python → JSearch indicator → JSearch v5
/search-v2 → JSearch provider → cross-source dedupe → admin initials (PII) → admin
simplify → tips + full report → app tracker → admin dashboard → search timeout refund →
orphan reconcile.
