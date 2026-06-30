"""Credit-pack catalog + checkout. Stripe-ready, with a test stub until keys are set.

To go live with Stripe later:
  1. pip install stripe
  2. set env: ROLEQUILL_PAYMENTS_MODE=stripe, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
     and a STRIPE_PRICE_* id per pack (or create prices inline).
  3. fill in `_stripe_checkout` and add a /credits/webhook handler that calls
     credits.add_purchase on checkout.session.completed.
Until then PAYMENTS_MODE=stub fulfills instantly so the whole flow is testable.
"""
import os

# price_usd is display only; stripe_price_id wires to a real Stripe Price when live.
PACKS = {
    "starter": {"id": "starter", "name": "Starter", "credits": 5, "price_usd": 5,
                "stripe_price_id": os.environ.get("STRIPE_PRICE_STARTER")},
    "plus": {"id": "plus", "name": "Plus", "credits": 15, "price_usd": 12,
             "stripe_price_id": os.environ.get("STRIPE_PRICE_PLUS")},
    "pro": {"id": "pro", "name": "Pro", "credits": 40, "price_usd": 25,
            "stripe_price_id": os.environ.get("STRIPE_PRICE_PRO")},
}


def list_packs():
    out = []
    for p in PACKS.values():
        per = p["price_usd"] / p["credits"]
        out.append({**p, "per_search": f"${per:.2f}"})
    return out


def get_pack(pack_id):
    return PACKS.get(pack_id)


def is_live(app) -> bool:
    return (app.config.get("PAYMENTS_MODE") == "stripe"
            and bool(app.config.get("STRIPE_SECRET_KEY")))


def create_checkout(app, pack, user, success_url, cancel_url):
    """Return a dict describing what the caller should do.

    {"mode": "stub"}                -> caller fulfills immediately (test mode)
    {"mode": "redirect", "url": ..} -> caller redirects the user to Stripe Checkout
    """
    if is_live(app):
        return _stripe_checkout(app, pack, user, success_url, cancel_url)
    return {"mode": "stub"}


def _stripe_checkout(app, pack, user, success_url, cancel_url):  # pragma: no cover
    import stripe
    stripe.api_key = app.config["STRIPE_SECRET_KEY"]
    line_item = (
        {"price": pack["stripe_price_id"], "quantity": 1}
        if pack.get("stripe_price_id") else
        {"price_data": {
            "currency": "usd",
            "product_data": {"name": f"RoleQuill {pack['name']} — {pack['credits']} credits"},
            "unit_amount": int(pack["price_usd"] * 100),
        }, "quantity": 1}
    )
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[line_item],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(user["id"]),
        metadata={"user_id": user["id"], "pack_id": pack["id"],
                  "credits": pack["credits"]},
    )
    return {"mode": "redirect", "url": session.url}
