from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

PLACEHOLDER_MARKERS = ("changeme", "dev-only")


def is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    app_env: Literal["development", "production"] = "development"
    flask_secret_key: str | None = None

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "marketpulse"
    db_user: str = "marketpulse"
    db_password: str = "changeme"

    watchlist_path: str = "config/watchlist.yaml"
    news_per_ticker_limit: int = 15
    news_max_workers: int = 8
    price_max_workers: int = 8
    outbound_min_interval_seconds: float = 0.25
    finbert_model_name: str = "ProsusAI/finbert"

    @model_validator(mode="after")
    def _real_db_password_in_production(self):
        if self.app_env == "production" and (not self.db_password or is_placeholder(self.db_password)):
            raise ValueError("DB_PASSWORD must be a real value when APP_ENV=production")
        return self

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def watchlist_abs_path(self) -> Path:
        p = Path(self.watchlist_path)
        return p if p.is_absolute() else REPO_ROOT / p


settings = Settings()
