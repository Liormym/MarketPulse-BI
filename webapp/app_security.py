"""Session, cookie, and proxy hardening, applied once when the Flask app is built.

Production (APP_ENV=production) refuses to start without a real session key,
and only trusts forwarded headers from the single Ingress hop in front of it.
Development keeps the convenience fallbacks.
"""
import os

from werkzeug.middleware.proxy_fix import ProxyFix

from marketpulse.config import is_placeholder

MIN_SECRET_LENGTH = 32


def _production_secret(secret: str | None) -> str:
    if not secret:
        raise RuntimeError("FLASK_SECRET_KEY must be set when APP_ENV=production")
    if len(secret) < MIN_SECRET_LENGTH or is_placeholder(secret):
        raise RuntimeError(
            "FLASK_SECRET_KEY is a placeholder or too short - generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return secret


def configure_app(app, env_settings) -> None:
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    if env_settings.app_env == "production":
        app.secret_key = _production_secret(env_settings.flask_secret_key)
        app.config["SESSION_COOKIE_SECURE"] = True
        # One trusted hop: the Ingress controller in front of the Service.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    else:
        app.secret_key = env_settings.flask_secret_key or os.urandom(24)
        app.config["SESSION_COOKIE_SECURE"] = False
