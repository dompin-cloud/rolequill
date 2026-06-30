"""Flask application factory."""
from flask import Flask

from .config import Config


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    # trust X-Forwarded-* when behind Cloudflare / a reverse proxy so url_for
    # builds correct https links and remote IPs are accurate
    if app.config.get("BEHIND_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from . import db
    db.init_app(app)

    # one-line visibility on optional integrations at startup
    from .config import DOTENV_LOADED, DOTENV_PATH
    key = app.config.get("SERPAPI_KEY") or ""
    gj = f"ENABLED (key len {len(key)})" if key else "disabled (no SERPAPI_KEY)"
    pay = app.config.get("PAYMENTS_MODE", "stub")
    env_state = f"found at {DOTENV_PATH}" if DOTENV_LOADED else f"NOT FOUND at {DOTENV_PATH}"
    print(f"[RoleQuill] .env: {env_state}")
    print(f"[RoleQuill] Database: {app.config.get('DATABASE')}")
    print(f"[RoleQuill] Google Jobs: {gj} | Payments: {pay}")

    # production safety warnings
    if not app.config.get("DEBUG"):
        if str(app.config.get("SECRET_KEY", "")).startswith("dev-"):
            print("[RoleQuill] WARNING: default SECRET_KEY in production — set ROLEQUILL_SECRET!")
        if pay == "stripe" and not (app.config.get("STRIPE_SECRET_KEY")
                                    and app.config.get("STRIPE_WEBHOOK_SECRET")):
            print("[RoleQuill] WARNING: PAYMENTS_MODE=stripe but STRIPE keys/webhook secret missing.")

    from .auth import bp as auth_bp
    from .main import bp as main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    import json as _json

    @app.template_filter("from_json")
    def _from_json(value):
        if not value:
            return []
        try:
            return _json.loads(value)
        except (ValueError, TypeError):
            return []

    return app
