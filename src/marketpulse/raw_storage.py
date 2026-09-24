"""Persists raw extraction payloads to disk before any processing.

Spec §5 step 2: keeping an untouched copy of what was received enables
debugging and replay without depending on the external source again.
"""
import json
from datetime import date
from pathlib import Path

from .config import REPO_ROOT

RAW_DIR = REPO_ROOT / "data" / "raw"


def save_raw(run_date: date, category: str, payload) -> Path:
    """category is e.g. 'prices' or 'news'. payload must be JSON-serializable."""
    day_dir = RAW_DIR / run_date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    out_path = day_dir / f"{category}.json"
    out_path.write_text(json.dumps(payload, default=str, ensure_ascii=False, indent=2))
    return out_path
