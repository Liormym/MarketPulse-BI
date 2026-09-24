"""Date normalization and headline cleaning (spec §5 step 4)."""
import re
from datetime import date

_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def date_to_key(d: date) -> int:
    return int(d.strftime("%Y%m%d"))


def clean_headline(title: str) -> str:
    # Collapse whitespace (incl. tabs/newlines) to single spaces before stripping
    # control chars, so a tab between words leaves a separator instead of gluing them.
    title = _WHITESPACE_RE.sub(" ", title)
    title = _CONTROL_CHARS_RE.sub("", title).strip()
    return title
