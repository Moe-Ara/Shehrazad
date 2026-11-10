"""Small shared helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "LIVE" if seconds is not None else "?:??"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
_UNIT_RE = re.compile(r"^(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s?)?$")


def parse_timestamp(text: str) -> float | None:
    """Parse '1:23', '01:02:03', '90', '1m30s' or '2h' into seconds."""
    text = text.strip().lower()
    if not text:
        return None
    if text.isdigit():
        return float(int(text))
    match = _TIME_RE.match(text)
    if match:
        hours = int(match.group(1) or 0)
        minutes, secs = int(match.group(2)), int(match.group(3))
        if secs >= 60 or (hours and minutes >= 60):
            return None
        return float(hours * 3600 + minutes * 60 + secs)
    match = _UNIT_RE.match(text)
    if match and any(match.groups()):
        hours, minutes, secs = (int(g or 0) for g in match.groups())
        return float(hours * 3600 + minutes * 60 + secs)
    return None


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def escape_markdown(text: str) -> str:
    return re.sub(r"([\\*_`~|>\[\]()])", r"\\\1", text)
