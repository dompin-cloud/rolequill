# RoleQuill — Automated Job Search Service

A multi-user Flask web app that runs a fully automated job search from a resume +
criteria. It scans the free **public ATS APIs** (Greenhouse, Lever, Ashby), filters
out ghost/evergreen, scam, and stale postings, scores every match against the
resume (with an ATS keyword estimate), provides **direct apply links**, and exports
an Excel workbook in the exact 6-tab format used in `Dominic_Job_Search_*.xlsx`.

Domain: **rolequill.com** (to be registered via Cloudflare).

## Features
- **Accounts** — register / login (hashed passwords), multi-user, per-user resumes & search history.
- **Resume parsing** — PDF / DOCX / TXT → text + detected skills (taxonomy in `app/pipeline/keywords.py`) **plus a section-aware profile** (Core Skills, Education, Professional Development / Certifications) in `app/pipeline/profile.py`, robust to PDF reflow.
- **Optional job title** — leave the title blank to rank purely on resume fit; skills/education/professional-development dominate the score (a `<2` keyword-overlap gate filters noise). Provide a title to focus on a specific role.
- **Clean salary handling** — structured pay objects from any feed are normalized to `$lo - $hi CUR` (`format_pay` + a JobPosting safety net); no raw dicts leak to the UI/Excel.
- **Hybrid provider layer** — pluggable; ships with Greenhouse, Lever, Ashby public feeds. Add paid aggregators by dropping a new class in `app/providers/`.
- **Quality filters** — drops ghost/talent-pipeline reqs, scam-signal posts, listings with no valid apply link, and stale (open >75 days) requisitions. ATS feeds only serve *open* roles, so closed jobs are excluded at the source.
- **Scoring** — 0–100 match score (title + skills + pay + work-type + location) and a separate 0–100 ATS keyword estimate, plus per-role "why it matches" and "missing skills".
- **Excel export** — 6 sheets matching the source template's colors, widths, merges, score-based row fills, and hyperlinked apply links.
- **Credits (freemium + pay-as-you-go)** — 1 credit = 1 successful search. New accounts get 3 free; +1 free/week (capped at 2, no rollover). Purchased credits never expire. Empty/failed searches are auto-refunded. Packs: Starter 5/$5, Plus 15/$12, Pro 40/$25. See `app/credits.py` and `app/payments.py`.

## Production & Stripe
See **[DEPLOY.md](DEPLOY.md)** for the full go-live guide (Stripe webhook setup,
hosting via VPS+Cloudflare or a PaaS, env vars, persistence, checklist).

## Payments (Stripe)
Runs in **test/stub mode** by default — "buy" adds credits instantly without charging,
so the whole flow is testable now. To go live with your Stripe account:
1. `pip install stripe`
2. Set env: `ROLEQUILL_PAYMENTS_MODE=stripe`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`,
   `STRIPE_WEBHOOK_SECRET` (and optionally `STRIPE_PRICE_STARTER/PLUS/PRO`).
3. Implement the webhook handler that calls `credits.add_purchase` on
   `checkout.session.completed` (integration point documented in `app/payments.py`).

## Run it
```powershell
cd "C:\Users\twizt\Downloads\Job Search Website"
pip install -r requirements.txt
python run.py
```
Then open http://127.0.0.1:5000 — sign up, upload a resume, run a search.

### Configuration (env vars)
| Var | Default | Meaning |
|-----|---------|---------|
| `JOBSEARCH_SECRET` | dev key | Flask session secret — **set this in production** |
| `JOBSEARCH_MAX_COMPANIES` | 60 | Companies scanned per provider per search |
| `JOBSEARCH_WORKERS` | 16 | Parallel HTTP workers |
| `SERPAPI_KEY` | _(unset)_ | Enables **Google Jobs** via SerpApi (see below) |
| `ROLEQUILL_GOOGLE_PAGES` | 1 | Google Jobs result pages per search (1 page ≈ 10 jobs = 1 SerpApi credit) |

## Google Jobs integration
Google for Jobs has **no free official API**, so RoleQuill reads it through **SerpApi**
(`app/providers/google_jobs.py`), a query-based provider that runs alongside the
per-company ATS feeds. It stays disabled until a key is present.

To turn it on (recommended — `.env` file, works regardless of which terminal/IDE
launches the app):
1. Sign up at https://serpapi.com (free tier = 100 searches/month) and copy your API key.
2. Copy `.env.example` to `.env` and set `SERPAPI_KEY=your_key_here`.
3. `python run.py` — startup prints `[RoleQuill] Google Jobs: ENABLED`, and the dashboard
   shows a "Google Jobs ✓ on" badge.

Every search then also queries Google Jobs (by title, or your top resume skills if no
title). Results are tagged `Google (<source board>)`, filtered/geo-checked/scored like
everything else, and **deduped** against ATS results by company+role. The results page
reports the per-search Google status (`ok — N postings` / `disabled` / `skipped` / `error`).

> Why a `.env` file instead of `$env:SERPAPI_KEY`? Shell env vars only apply to the
> terminal that set them. If the app is started elsewhere (another terminal, an IDE
> preview/launcher), it won't see them — which shows up as `Google Jobs: disabled`.
> The `.env` file is read from disk at startup, so it always loads. (A real OS env var,
> if present, still takes precedence.)

Cost control: each result page is 1 SerpApi credit. Keep `ROLEQUILL_GOOGLE_PAGES=1`
on the free tier. Swapping in a different aggregator (Apify, ScrapingDog, etc.) is just
another class in `app/providers/` — the query-provider plumbing is already there.

## Project layout
```
run.py                     entry point
app/
  __init__.py              app factory + from_json filter
  config.py  db.py  auth.py  main.py  search_runner.py
  providers/               base.py, greenhouse.py, lever.py, ashby.py, companies.py
  pipeline/                keywords.py, resume.py, filters.py, scoring.py,
                           __init__.py (orchestrator), excel_export.py
  templates/  static/
data/                      sqlite db, uploaded resumes, generated .xlsx (gitignored)
```

## Extending coverage
- **More companies:** add board tokens to the lists in `app/providers/companies.py`. Unknown tokens just return zero jobs, so additions are safe.
- **More boards / paid aggregators:** subclass `Provider` in `app/providers/base.py`, implement `fetch()`, and register it in `app/providers/__init__.py`.
- **Skill taxonomy:** edit `SKILLS` in `app/pipeline/keywords.py` to tune skill detection, ATS scoring, and gap analysis.

## Notes & limits
- Greenhouse/Lever/Ashby feeds are **per-company**, so "scan all boards" = scan a curated company list (seeded with ~115 tech employers). Broaden it as needed.
- Salary and exact post dates are only as good as each feed exposes them; the app labels missing salary as "Not stated".
- This is an MVP foundation for a service: add a task queue (Celery/RQ) and Postgres before scaling beyond a handful of concurrent users.
