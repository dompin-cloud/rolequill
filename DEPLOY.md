# RoleQuill — Production Deployment & Stripe

This covers (1) connecting Stripe and (2) putting the app online at rolequill.com.

---

## Part 1 — Connect Stripe

The code is already Stripe-ready. You only do dashboard + env setup.

1. **Create your Stripe account** (you have one) and grab API keys from
   https://dashboard.stripe.com/apikeys:
   - `STRIPE_SECRET_KEY` (starts `sk_live_…` for live, `sk_test_…` to test first)
   - `STRIPE_PUBLISHABLE_KEY` (`pk_live_…`)

2. **(Optional) Create Products/Prices** in Stripe for the three packs, or skip this —
   the app creates prices inline from `app/payments.py` (`$5 / $12 / $25`). If you make
   them in Stripe, copy each Price id into `STRIPE_PRICE_STARTER/PLUS/PRO`.

3. **Add a webhook endpoint** at https://dashboard.stripe.com/webhooks:
   - URL: `https://rolequill.com/credits/webhook`
   - Event to send: `checkout.session.completed`
   - Copy the signing secret (`whsec_…`) into `STRIPE_WEBHOOK_SECRET`.

4. **Set env (in `.env` or host config) and switch to live:**
   ```
   ROLEQUILL_PAYMENTS_MODE=stripe
   STRIPE_SECRET_KEY=sk_live_xxx
   STRIPE_PUBLISHABLE_KEY=pk_live_xxx
   STRIPE_WEBHOOK_SECRET=whsec_xxx
   ```
   Restart. Startup prints `Payments: stripe`. (If keys are missing it warns.)

5. **How it flows:** user clicks Buy → `create_checkout` makes a Stripe Checkout
   Session → user pays on Stripe → Stripe POSTs `checkout.session.completed` to
   `/credits/webhook` → we verify the signature and `add_purchase` the credits
   (idempotent via the `stripe_events` table, so retries never double-credit).

6. **Test before going live:** use `sk_test_…` keys + Stripe's test card `4242 4242
   4242 4242`, and run `stripe listen --forward-to localhost:5000/credits/webhook`
   (Stripe CLI) to exercise the webhook locally.

---

## Part 2 — Deploy the app

### What needs to change from dev
- **Don't use the Flask dev server.** Use gunicorn (Linux) or waitress (any OS):
  ```
  gunicorn -w 2 --threads 8 --timeout 120 -b 0.0.0.0:8000 wsgi:app
  waitress-serve --listen=0.0.0.0:8000 wsgi:app
  ```
  Background searches are in-process threads, so keep workers low and threads high.
- **Set production env:** `ROLEQUILL_DEBUG=0`, `ROLEQUILL_HTTPS=1`,
  `ROLEQUILL_BEHIND_PROXY=1`, and a strong `ROLEQUILL_SECRET`
  (`python -c "import secrets; print(secrets.token_hex(32))"`).
- **Persist `data/`** — SQLite db, uploaded resumes, and generated Excel live there.
  On any host, mount a persistent disk at the project's `data/` path or it resets on
  redeploy.

### Recommended path: a small VPS behind Cloudflare
Cheapest and most control; works great with your Cloudflare domain.

1. **Register `rolequill.com`** (Cloudflare Registrar) and keep DNS on Cloudflare.
2. **Provision a VPS** (DigitalOcean/Hetzner/Linode, ~$5–6/mo, Ubuntu).
3. On the server:
   ```bash
   git clone <your repo>  &&  cd "Job Search Website"
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env   # fill in real values
   ```
4. **Run it as a service** (systemd) so it restarts on boot/crash:
   ```ini
   # /etc/systemd/system/rolequill.service
   [Service]
   WorkingDirectory=/opt/rolequill/Job Search Website
   ExecStart=/opt/rolequill/Job Search Website/.venv/bin/gunicorn -w 2 --threads 8 --timeout 120 -b 127.0.0.1:8000 wsgi:app
   Restart=always
   [Install]
   WantedBy=multi-user.target
   ```
   `sudo systemctl enable --now rolequill`
5. **Put nginx in front** (TLS termination + proxy to 127.0.0.1:8000), or simpler:
   use a **Cloudflare Tunnel** (`cloudflared`) to expose the local port with no open
   inbound ports and automatic TLS.
6. **Cloudflare DNS:** point `rolequill.com` (A/AAAA or the tunnel CNAME) at the
   server; enable proxy (orange cloud) for TLS + CDN. SSL/TLS mode: Full (strict).

### Easier path: a managed PaaS (Render / Railway / Fly.io)
Less ops, slightly more cost. Render example:
1. New **Web Service** from your repo.
2. Build: `pip install -r requirements.txt`  ·  Start: `gunicorn -w 2 --threads 8 -b 0.0.0.0:$PORT wsgi:app` (the included `Procfile` already does this).
3. Add a **persistent disk** mounted at `.../data`.
4. Set env vars in the dashboard (same as `.env`).
5. Add custom domain `rolequill.com`; in Cloudflare, CNAME to the Render URL
   (DNS-only or proxied). TLS is automatic.

---

## Go-live checklist
- [ ] `ROLEQUILL_SECRET` set to a strong random value
- [ ] `ROLEQUILL_DEBUG=0`, `ROLEQUILL_HTTPS=1`, `ROLEQUILL_BEHIND_PROXY=1`
- [ ] `SERPAPI_KEY` set (startup shows `Google Jobs: ENABLED`)
- [ ] Stripe live keys + webhook secret set; `Payments: stripe`; webhook points to `/credits/webhook`
- [ ] `data/` on a persistent disk; back it up
- [ ] Served by gunicorn/waitress (not `python run.py`)
- [ ] rolequill.com resolves via Cloudflare with TLS (Full strict)
- [ ] Did a real test purchase (test mode) and saw credits land via the webhook

## Scaling notes (later, not needed to launch)
- Move SQLite → Postgres when you have steady concurrent users (the data layer is
  plain SQL; queries port over with minor tweaks).
- Move in-process search threads → a task queue (RQ/Celery + Redis) for reliability.
- Add the employer/recruiter side + affiliates as the real revenue layer.
