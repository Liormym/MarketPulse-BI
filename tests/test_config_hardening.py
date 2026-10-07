import pytest
from pydantic import ValidationError

from marketpulse.config import Settings


def _settings(monkeypatch, **env):
    for key in ("APP_ENV", "DB_PASSWORD", "FLASK_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_development_allows_placeholder_db_password(monkeypatch):
    s = _settings(monkeypatch, APP_ENV="development", DB_PASSWORD="changeme")
    assert s.db_password == "changeme"


def test_production_rejects_placeholder_db_password(monkeypatch):
    with pytest.raises(ValidationError, match="DB_PASSWORD"):
        _settings(monkeypatch, APP_ENV="production", DB_PASSWORD="changeme")


def test_production_rejects_empty_db_password(monkeypatch):
    with pytest.raises(ValidationError, match="DB_PASSWORD"):
        _settings(monkeypatch, APP_ENV="production", DB_PASSWORD="")


def test_production_accepts_real_db_password(monkeypatch):
    s = _settings(monkeypatch, APP_ENV="production", DB_PASSWORD="s3cure-Real-Pass")
    assert s.app_env == "production"


def test_unknown_app_env_is_rejected(monkeypatch):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, APP_ENV="prod")


def test_outbound_knobs_have_bounded_defaults(monkeypatch):
    s = _settings(monkeypatch)
    assert 0 < s.outbound_min_interval_seconds <= 1
    assert 1 <= s.news_max_workers <= 16
    assert 1 <= s.price_max_workers <= 16
