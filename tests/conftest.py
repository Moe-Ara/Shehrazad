from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from musicbot.db import Database
from musicbot.models import ResolveResult, StreamInfo, Track
from musicbot.sources import SourceError


def make_track(n: int | str, *, duration: float | None = 180.0, requester: int | None = None) -> Track:
    return Track(title=f"Track {n}", url=f"https://www.youtube.com/watch?v=t{n}", provider="youtube",
                 duration=duration, requester_id=requester)


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


class Clock:
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


# --------------------------------------------------------------------------- player fakes


class FakeSource:
    def __init__(self, stream: StreamInfo, seek: float, volume: float) -> None:
        self.stream, self.seek, self.volume = stream, seek, volume


class FakeVoice:
    """Mimics discord.VoiceClient: play() runs until finish()/stop() fires `after`."""

    def __init__(self, channel_id: int = 1) -> None:
        self.channel = type("Channel", (), {"id": channel_id, "members": []})()
        self.source: FakeSource | None = None
        self._after: Any = None
        self._playing = False
        self._paused = False
        self.connected = True
        self.played: list[FakeSource] = []

    def is_connected(self) -> bool:
        return self.connected

    def is_playing(self) -> bool:
        return self._playing and not self._paused

    def is_paused(self) -> bool:
        return self._playing and self._paused

    def play(self, source: FakeSource, *, after: Any) -> None:
        assert not self._playing, "play() while already playing"
        self.source, self._after, self._playing, self._paused = source, after, True, False
        self.played.append(source)

    def _end(self, error: Exception | None = None) -> None:
        if not self._playing:
            return
        self._playing = self._paused = False
        after, self._after = self._after, None
        after(error)  # discord.py calls this from its audio thread

    def finish(self, error: Exception | None = None) -> None:
        self._end(error)

    def stop(self) -> None:
        self._end(None)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    async def disconnect(self, *, force: bool = False) -> None:
        self._end(None)
        self.connected = False


class FakeRegistry:
    def __init__(self) -> None:
        self.failures: dict[str, list[SourceError]] = {}
        self.stream_calls: list[str] = []
        self.invalidated: list[str] = []

    async def stream(self, track: Track) -> StreamInfo:
        self.stream_calls.append(track.url)
        pending = self.failures.get(track.url)
        if pending:
            raise pending.pop(0)
        return StreamInfo(url=f"stream:{track.url}", duration=track.duration)

    async def resolve(self, query: str, *, max_tracks: int) -> ResolveResult:
        return ResolveResult([make_track(query)])

    def show_artwork(self, track: Track) -> bool:
        return False

    def invalidate(self, track: Track) -> None:
        self.invalidated.append(track.url)


@dataclass
class FakeHooks:
    events: list[tuple[str, Any]] = field(default_factory=list)
    started: list[Track] = field(default_factory=list)
    errors: list[tuple[Track, SourceError]] = field(default_factory=list)
    ended: list[str] = field(default_factory=list)
    snapshots: int = 0

    async def render_panel(self, player: Any) -> None:
        self.events.append(("render", player.state))

    async def notify(self, player: Any, key: str, **params: Any) -> None:
        self.events.append(("notify", key))

    async def on_track_start(self, player: Any, track: Track) -> None:
        self.started.append(track)

    async def on_track_error(self, player: Any, track: Track, error: SourceError) -> None:
        self.errors.append((track, error))

    async def on_session_end(self, player: Any, reason: str) -> None:
        self.ended.append(reason)

    async def save_snapshot(self, player: Any) -> None:
        self.snapshots += 1


async def settle(cycles: int = 20) -> None:
    """Let the player loop and scheduled callbacks run."""
    for _ in range(cycles):
        await asyncio.sleep(0)
