"""Per-guild track queue: pure, synchronous and fully unit-testable.

The queue holds the current track, the upcoming tracks and a bounded history
used by `previous`. All positions in the public API are 1-based to match
what users see in /queue.
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Iterable
from enum import StrEnum

from .models import Track


class LoopMode(StrEnum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"

    def next(self) -> LoopMode:
        order = [LoopMode.OFF, LoopMode.TRACK, LoopMode.QUEUE]
        return order[(order.index(self) + 1) % len(order)]


class QueueError(Exception):
    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


class TrackQueue:
    def __init__(self, max_size: int = 200, history_size: int = 50) -> None:
        self.max_size = max_size
        self.current: Track | None = None
        self.upcoming: list[Track] = []
        self.history: deque[Track] = deque(maxlen=history_size)
        self.loop = LoopMode.OFF
        self.version = 0  # bumped on every mutation; used for debounced persistence

    def _touch(self) -> None:
        self.version += 1

    def __len__(self) -> int:
        return len(self.upcoming)

    @property
    def is_empty(self) -> bool:
        return self.current is None and not self.upcoming

    @property
    def total_duration(self) -> float:
        return sum(t.duration or 0 for t in self.upcoming)

    @property
    def free_slots(self) -> int:
        return max(0, self.max_size - len(self.upcoming))

    def add(self, tracks: Iterable[Track], *, position: int | None = None) -> int:
        """Add tracks at the end (or before 1-based `position`). Returns count added."""
        incoming = list(tracks)
        if not incoming:
            return 0
        room = self.free_slots
        if room <= 0:
            raise QueueError("queue.full", limit=self.max_size)
        incoming = incoming[:room]
        if position is None:
            self.upcoming.extend(incoming)
        else:
            index = max(0, min(position - 1, len(self.upcoming)))
            self.upcoming[index:index] = incoming
        self._touch()
        return len(incoming)

    def _check_index(self, position: int) -> int:
        if not self.upcoming:
            raise QueueError("queue.empty")
        if position < 1 or position > len(self.upcoming):
            raise QueueError("queue.bad_position", max=len(self.upcoming))
        return position - 1

    def remove(self, position: int) -> Track:
        index = self._check_index(position)
        track = self.upcoming.pop(index)
        self._touch()
        return track

    def move(self, source: int, target: int) -> Track:
        index = self._check_index(source)
        target_index = self._check_index(target)
        track = self.upcoming.pop(index)
        self.upcoming.insert(target_index, track)
        self._touch()
        return track

    def clear(self) -> int:
        count = len(self.upcoming)
        self.upcoming.clear()
        self._touch()
        return count

    def shuffle(self, rng: random.Random | None = None) -> None:
        if len(self.upcoming) < 2:
            raise QueueError("queue.too_short_to_shuffle")
        (rng or random).shuffle(self.upcoming)
        self._touch()

    def start_next(self) -> Track | None:
        """Promote the next upcoming track when nothing is current."""
        if self.current is None and self.upcoming:
            self.current = self.upcoming.pop(0)
            self._touch()
        return self.current

    def advance(self, *, skipped: bool = False) -> Track | None:
        """Finish the current track and move on, honouring the loop mode.

        A skip always moves on, even with LoopMode.TRACK.
        """
        finished = self.current
        if finished is not None:
            if self.loop is LoopMode.TRACK and not skipped:
                return finished
            self.history.append(finished)
            if self.loop is LoopMode.QUEUE:
                self.upcoming.append(finished)
        self.current = self.upcoming.pop(0) if self.upcoming else None
        self._touch()
        return self.current

    def previous(self) -> Track:
        """Go back to the last finished track; the current one goes back on top."""
        if not self.history:
            raise QueueError("queue.no_previous")
        track = self.history.pop()
        if self.current is not None:
            self.upcoming.insert(0, self.current)
        if self.loop is LoopMode.QUEUE and self.upcoming and self.upcoming[-1].entry_id == track.entry_id:
            self.upcoming.pop()  # it was re-queued at the end by queue-loop
        self.current = track
        self._touch()
        return track

    def jump(self, position: int) -> Track:
        """Play the track at `position` next-now; tracks before it are dropped."""
        index = self._check_index(position)
        target = self.upcoming[index]
        skipped = self.upcoming[:index]
        del self.upcoming[: index + 1]
        if self.current is not None:
            self.history.append(self.current)
        self.history.extend(skipped)
        if self.loop is LoopMode.QUEUE:
            # Queue-loop keeps everything: what we jumped over goes to the end.
            if self.current is not None:
                self.upcoming.append(self.current)
            self.upcoming.extend(skipped)
        self.current = target
        self._touch()
        return target

    def stop(self) -> None:
        self.current = None
        self.upcoming.clear()
        self._touch()

    def snapshot(self) -> dict[str, object]:
        return {
            "current": self.current.to_dict() if self.current else None,
            "upcoming": [t.to_dict() for t in self.upcoming],
            "loop": self.loop.value,
        }

    def restore(self, data: dict[str, object]) -> int:
        tracks: list[Track] = []
        current = data.get("current")
        if isinstance(current, dict):
            tracks.append(Track.from_dict(current))
        upcoming = data.get("upcoming")
        if isinstance(upcoming, list):
            tracks.extend(Track.from_dict(t) for t in upcoming if isinstance(t, dict))
        loop = data.get("loop")
        if isinstance(loop, str) and loop in LoopMode._value2member_map_:
            self.loop = LoopMode(loop)
        return self.add(tracks) if tracks else 0
