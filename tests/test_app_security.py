import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

from app_security import configure_app  # noqa: E402

GOOD_SECRET = "a3f9c1e7b2d84f6a9e0c5b7d1f2e3a4b6c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f"


def _app_with_scheme_route():
    app = Flask(__name__)

    @app.route("/scheme")
    def scheme():
        return request.scheme

    return app


def _cfg(**overrides):
    base = dict(app_env="development", flask_secret_key=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_production_refuses_missing_secret():
    with pytest.raises(RuntimeError, match="must be set"):
        configure_app(_app_with_scheme_route(), _cfg(app_env="production"))


@pytest.mark.parametrize("bad", ["changeme-generate-a-real-random-value", "dev-only-change-me", "short"])
def test_production_refuses_placeholder_or_short_secret(bad):
    with pytest.raises(RuntimeError):
        configure_app(_app_with_scheme_route(), _cfg(app_env="production", flask_secret_key=bad))


def test_production_hardens_cookies_and_trusts_one_proxy_hop():
    app = _app_with_scheme_route()
    configure_app(app, _cfg(app_env="production", flask_secret_key=GOOD_SECRET))

    assert app.secret_key == GOOD_SECRET
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert isinstance(app.wsgi_app, ProxyFix)


def test_production_honours_forwarded_proto_from_ingress():
    app = _app_with_scheme_route()
    configure_app(app, _cfg(app_env="production", flask_secret_key=GOOD_SECRET))

    response = app.test_client().get("/scheme", headers={"X-Forwarded-Proto": "https"})

    assert response.get_data(as_text=True) == "https"


def test_development_keeps_convenience_defaults_and_ignores_forwarded_headers():
    app = _app_with_scheme_route()
    configure_app(app, _cfg(app_env="development"))

    assert app.secret_key
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert not isinstance(app.wsgi_app, ProxyFix)

    response = app.test_client().get("/scheme", headers={"X-Forwarded-Proto": "https"})
    assert response.get_data(as_text=True) == "http"
