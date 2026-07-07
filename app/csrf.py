"""Lightweight CSRF protection — stdlib only, no Flask-WTF dependency.

A per-session random token is required on every state-changing request (POST/PUT/
PATCH/DELETE). Templates render it into a hidden field via `{{ csrf_token() }}`;
the `before_request` hook validates the submitted value against the session with a
constant-time compare. Safe methods (GET/HEAD/OPTIONS) and explicitly exempted
endpoints (e.g. the signature-verified Stripe webhook) are skipped.

Relies on the session cookie, which is HttpOnly + SameSite=Lax + Secure-in-prod, so
the token can't be read cross-origin and cross-site POSTs are already blunted; the
token adds defense-in-depth.
"""
import hmac
import secrets

from flask import abort, request, session

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
FIELD_NAME = "csrf_token"
_SESSION_KEY = "_csrf_token"
_EXEMPT_ENDPOINTS = set()


def csrf_exempt(endpoint):
    """Skip CSRF validation for a view endpoint (e.g. 'main.stripe_webhook')."""
    _EXEMPT_ENDPOINTS.add(endpoint)


def generate_token():
    """Return the session's CSRF token, creating one on first use."""
    tok = session.get(_SESSION_KEY)
    if not tok:
        tok = secrets.token_urlsafe(32)
        session[_SESSION_KEY] = tok
    return tok


def _submitted_token():
    return (request.form.get(FIELD_NAME)
            or request.headers.get("X-CSRFToken")
            or request.headers.get("X-CSRF-Token"))


def init_app(app):
    @app.before_request
    def _validate():
        if request.method in SAFE_METHODS:
            return
        if request.endpoint in _EXEMPT_ENDPOINTS:
            return
        expected = session.get(_SESSION_KEY)
        sent = _submitted_token()
        if not expected or not sent or not hmac.compare_digest(str(sent), str(expected)):
            abort(400, description="CSRF token missing or invalid. Reload the page and try again.")

    @app.context_processor
    def _inject_token():
        # exposed as a callable so a token is minted only for templates that use it
        return {"csrf_token": generate_token}
