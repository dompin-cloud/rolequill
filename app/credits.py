"""Credit economy: free weekly refill + never-expiring purchased credits.

Design (see pricing decision):
- 1 credit = 1 successful search run (scan + score + Excel).
- New accounts get a SIGNUP_BONUS of free credits.
- Free credits refill +FREE_WEEKLY per 7 days, capped at FREE_CAP, no rollover beyond cap.
- Purchased (paid) credits never expire.
- Spend draws from free first, then paid. Failed/empty searches are refunded.

All functions take an open sqlite connection so they work in both the request
context (get_db) and the background search thread (standalone_connection).
"""
from datetime import datetime, timedelta, timezone

SIGNUP_BONUS = 3
FREE_WEEKLY = 1
FREE_CAP = 2
REFILL_DAYS = 7
SEARCH_COST = 1


def _now():
    return datetime.now(timezone.utc)


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def balance(db, user_id) -> dict:
    row = db.execute("SELECT free_credits, paid_credits FROM users WHERE id = ?",
                     (user_id,)).fetchone()
    free = row["free_credits"] if row else 0
    paid = row["paid_credits"] if row else 0
    return {"free": free, "paid": paid, "total": free + paid}


def _log(db, user_id, delta, kind, note=""):
    bal = balance(db, user_id)["total"]
    db.execute(
        "INSERT INTO credit_ledger (user_id, delta, kind, balance_after, note) "
        "VALUES (?,?,?,?,?)", (user_id, delta, kind, bal, note))


def grant_signup(db, user_id):
    db.execute(
        "UPDATE users SET free_credits = free_credits + ?, free_reset_at = ? "
        "WHERE id = ?", (SIGNUP_BONUS, _now().isoformat(timespec="seconds"), user_id))
    _log(db, user_id, SIGNUP_BONUS, "signup", "Welcome credits")
    db.commit()


def ensure_weekly_refill(db, user_id):
    """Grant the weekly free credit if due and below the cap. Idempotent per request."""
    row = db.execute(
        "SELECT free_credits, free_reset_at FROM users WHERE id = ?",
        (user_id,)).fetchone()
    if row is None:
        return
    last = _parse(row["free_reset_at"])
    due = last is None or (_now() - last) >= timedelta(days=REFILL_DAYS)
    if due and row["free_credits"] < FREE_CAP:
        new_free = min(FREE_CAP, row["free_credits"] + FREE_WEEKLY)
        db.execute(
            "UPDATE users SET free_credits = ?, free_reset_at = ? WHERE id = ?",
            (new_free, _now().isoformat(timespec="seconds"), user_id))
        _log(db, user_id, new_free - row["free_credits"], "weekly", "Weekly free credit")
        db.commit()


def charge_search(db, user_id) -> str | None:
    """Spend one credit (free first, then paid). Returns the bucket or None if broke."""
    row = db.execute("SELECT free_credits, paid_credits FROM users WHERE id = ?",
                     (user_id,)).fetchone()
    if row is None:
        return None
    if row["free_credits"] >= SEARCH_COST:
        db.execute("UPDATE users SET free_credits = free_credits - ? WHERE id = ?",
                   (SEARCH_COST, user_id))
        source = "free"
    elif row["paid_credits"] >= SEARCH_COST:
        db.execute("UPDATE users SET paid_credits = paid_credits - ? WHERE id = ?",
                   (SEARCH_COST, user_id))
        source = "paid"
    else:
        return None
    _log(db, user_id, -SEARCH_COST, "search", f"Search run ({source})")
    db.commit()
    return source


def refund_search(db, user_id, source):
    if source not in ("free", "paid"):
        return
    col = "free_credits" if source == "free" else "paid_credits"
    db.execute(f"UPDATE users SET {col} = {col} + ? WHERE id = ?",
               (SEARCH_COST, user_id))
    _log(db, user_id, SEARCH_COST, "refund", "Refund: no/failed results")
    db.commit()


def add_purchase(db, user_id, credits, note="Credit pack purchase"):
    db.execute("UPDATE users SET paid_credits = paid_credits + ? WHERE id = ?",
               (credits, user_id))
    _log(db, user_id, credits, "purchase", note)
    db.commit()
