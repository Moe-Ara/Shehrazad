import random

import pytest

from musicbot.queue import LoopMode, QueueError, TrackQueue
from tests.conftest import make_track


def titles(tracks):
    return [t.title for t in tracks]


def test_add_respects_capacity_and_reports_added():
    q = TrackQueue(max_size=3)
    assert q.add([make_track(i) for i in range(5)]) == 3
    with pytest.raises(QueueError) as exc:
        q.add([make_track(9)])
    assert exc.value.key == "queue.full"


def test_add_at_position_inserts_before():
    q = TrackQueue()
    q.add([make_track(1), make_track(2)])
    q.add([make_track("x")], position=1)
    assert titles(q.upcoming) == ["Track x", "Track 1", "Track 2"]


def test_remove_and_move_validate_positions():
    q = TrackQueue()
    with pytest.raises(QueueError) as exc:
        q.remove(1)
    assert exc.value.key == "queue.empty"
    q.add([make_track(i) for i in range(1, 4)])
    with pytest.raises(QueueError) as exc:
        q.remove(4)
    assert exc.value.key == "queue.bad_position"
    q.move(3, 1)
    assert titles(q.upcoming) == ["Track 3", "Track 1", "Track 2"]
    assert q.remove(2).title == "Track 1"


def test_advance_loop_modes():
    q = TrackQueue()
    q.add([make_track(1), make_track(2)])
    assert q.start_next().title == "Track 1"
    q.loop = LoopMode.TRACK
    assert q.advance().title == "Track 1"  # repeats
    assert q.advance(skipped=True).title == "Track 2"  # skip breaks track-loop
    q.loop = LoopMode.QUEUE
    assert q.advance().title == "Track 2"  # a one-track queue loops onto itself
    q.add([make_track(3)])
    assert q.advance().title == "Track 3"
    assert titles(q.upcoming) == ["Track 2"]  # finished track went to the back
    assert q.advance().title == "Track 2"
    q.loop = LoopMode.OFF
    q.advance()
    assert q.advance() is None
    assert q.is_empty


def test_previous_restores_order():
    q = TrackQueue()
    q.add([make_track(1), make_track(2), make_track(3)])
    q.start_next()
    q.advance()
    assert q.current.title == "Track 2"
    assert q.previous().title == "Track 1"
    assert titles(q.upcoming) == ["Track 2", "Track 3"]
    with pytest.raises(QueueError):
        q.previous()


def test_jump_drops_skipped_tracks_unless_queue_loop():
    q = TrackQueue()
    q.add([make_track(i) for i in range(1, 6)])
    q.start_next()  # current = 1, upcoming = 2..5
    assert q.jump(3).title == "Track 4"
    assert titles(q.upcoming) == ["Track 5"]
    assert titles(q.history) == ["Track 1", "Track 2", "Track 3"]

    q2 = TrackQueue()
    q2.add([make_track(i) for i in range(1, 5)])
    q2.start_next()
    q2.loop = LoopMode.QUEUE
    q2.jump(2)  # -> Track 3; Track 1 (current) and Track 2 go to the end
    assert q2.current.title == "Track 3"
    assert titles(q2.upcoming) == ["Track 4", "Track 1", "Track 2"]


def test_shuffle_requires_two_and_keeps_tracks():
    q = TrackQueue()
    q.add([make_track(1)])
    with pytest.raises(QueueError):
        q.shuffle()
    q.add([make_track(i) for i in range(2, 10)])
    before = sorted(titles(q.upcoming))
    q.shuffle(random.Random(1))
    assert sorted(titles(q.upcoming)) == before


def test_snapshot_roundtrip_and_version_bumps():
    q = TrackQueue()
    v0 = q.version
    q.add([make_track(1, requester=42), make_track(2)])
    q.start_next()
    q.loop = LoopMode.QUEUE
    assert q.version > v0
    restored = TrackQueue()
    assert restored.restore(q.snapshot()) == 2
    assert titles(restored.upcoming) == ["Track 1", "Track 2"]
    assert restored.upcoming[0].requester_id == 42
    assert restored.loop is LoopMode.QUEUE


def test_restore_ignores_garbage():
    q = TrackQueue()
    assert q.restore({"current": "nope", "upcoming": [1, None, {"title": "a", "url": "https://x"}], "loop": "bogus"}) == 1
    assert q.loop is LoopMode.OFF
