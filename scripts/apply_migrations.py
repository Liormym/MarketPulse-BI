"""Applies versioned SQL migrations from db/migrations/ in filename order.

Tracks applied versions in the schema_migrations table so re-running is a no-op.
Usage: python scripts/apply_migrations.py
"""
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from marketpulse.config import settings  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"


def main() -> None:
    conn = psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
    )
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(50) PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        conn.commit()

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.stem
            if version in applied:
                print(f"skip  {version} (already applied)")
                continue
            print(f"apply {version}")
            sql = path.read_text()
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s) "
                    "ON CONFLICT (version) DO NOTHING",
                    (version,),
                )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print("migrations up to date")


if __name__ == "__main__":
    main()
