"""Core value types shared across the bot."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class Track:
    title: str
    url: str
    provider: str
    duration: float | None = None
    artist: str | None = None
    artwork_url: str | None = None
    requester_id: int | None = None
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10], compare=False)

    def requested_by(self, user_id: int | None) -> Track:
        return replace(self, requester_id=user_id, entry_id=uuid.uuid4().hex[:10])

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("entry_id", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Track:
        return cls(
            title=str(data["title"]),
            url=str(data["url"]),
            provider=str(data.get("provider") or "youtube"),
            duration=float(data["duration"]) if data.get("duration") is not None else None,
            artist=data.get("artist"),
            artwork_url=data.get("artwork_url"),
            requester_id=int(data["requester_id"]) if data.get("requester_id") else None,
        )


@dataclass(frozen=True)
class StreamInfo:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    duration: float | None = None
    is_live: bool = False


@dataclass(frozen=True)
class ResolveResult:
    tracks: list[Track]
    playlist_title: str | None = None
    truncated: bool = False

    @property
    def is_playlist(self) -> bool:
        return self.playlist_title is not None
