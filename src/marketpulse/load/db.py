from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from ..config import settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(settings.database_url, future=True)
