from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "marketpulse"
    db_user: str = "marketpulse"
    db_password: str = "changeme"

    watchlist_path: str = "config/watchlist.yaml"
    news_per_ticker_limit: int = 15
    request_throttle_seconds: float = 1.0
    finbert_model_name: str = "ProsusAI/finbert"

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
