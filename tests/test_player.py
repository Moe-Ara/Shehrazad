import asyncio

import pytest

from musicbot import player as player_mod
from musicbot.player import GuildPlayer, PlayerState
from musicbot.queue import LoopMode, QueueError
from musicbot.sources import ProviderUnavailable, TrackUnavailable
from tests.conftest import FakeHooks, FakeRegistry, FakeSource, FakeVoice, make_track, settle


def build(guild_id=1, *, idle=None, max_queue=200, registry=None, hooks=None):
    registry = registry or FakeRegistry()
    hooks = hooks or FakeHooks()
    p = GuildPlayer(
        guild_id,
        registry=registry,  # type: ignore[arg-type]
        hooks=hooks,
        idle_timeout=lambda: idle,
        max_queue=lambda: max_queue,
        source_factory=FakeSource,
        retry_delay=0,
    )
    voice = FakeVoice()
    p.attach(voice)
    return p, voice, registry, hooks


async def test_plays_queue_in_order_and_goes_idle():
    p, voice, _, hooks = build()
    await p.enqueue([make_track(1), make_track(2)])
    await settle()
    assert p.state is PlayerState.PLAYING and p.current.title == "Track 1"
    voice.finish()
    await settle()
    assert p.current.title == "Track 2"
    voice.finish()
    await settle()
    assert p.state is PlayerState.IDLE and p.current is None
    assert [t.title for t in hooks.started] == ["Track 1", "Track 2"]
    await p.shutdown("test")


async def test_skip_previous_and_loop_track():
    p, voice, _, _ = build()
    await p.enqueue([make_track(1), make_track(2), make_track(3)])
    await settle()
    await p.skip()
    await settle()
    assert p.current.title == "Track 2"
    await p.previous()
    await settle()
    assert p.current.title == "Track 1"
    await p.set_loop(LoopMode.TRACK)
    voice.finish()
    await settle()
    assert p.current.title == "Track 1"  # repeated
    await p.skip()  # skip overrides track-loop
    await settle()
    assert p.current.title == "Track 2"
    await p.shutdown("test")


async def test_seek_respawns_same_track_at_offset_without_recording_history_twice():
    p, voice, _, hooks = build()
    await p.enqueue([make_track(1, duration=300)])
    await settle()
    await p.seek(120)
    await settle()
    assert voice.played[-1].seek == 120 and p.current.title == "Track 1"
    assert p.position == pytest.approx(120, abs=1)
    assert len(hooks.started) == 1
    with pytest.raises(QueueError):
        await p.seek(301)
    await p.shutdown("test")


async def test_pause_resume_tracks_position():
    p, voice, _, _ = build()
    await p.enqueue([make_track(1)])
    await settle()
    assert await p.pause() and p.state is PlayerState.PAUSED
    assert not await p.pause()
    frozen = p.position
    await asyncio.sleep(0.05)
    assert p.position == frozen
    assert await p.resume() and p.state is PlayerState.PLAYING
    await p.shutdown("test")


async def test_skip_while_paused_moves_on():
    p, voice, _, _ = build()
    await p.enqueue([make_track(1), make_track(2)])
    await settle()
    await p.pause()
    await p.skip()
    await settle()
    assert p.current.title == "Track 2" and p.state is PlayerState.PLAYING
    await p.shutdown("test")


async def test_permanently_unavailable_track_is_skipped_with_notice():
    registry = FakeRegistry()
    bad = make_track("bad")
    registry.failures[bad.url] = [TrackUnavailable("gone")]
    p, voice, _, hooks = build(registry=registry)
    await p.enqueue([bad, make_track(2)])
    await settle()
    assert p.current.title == "Track 2"
    assert hooks.errors[0][0].title == "Track bad"
    await p.shutdown("test")


async def test_transient_error_is_retried_once():
    registry = FakeRegistry()
    flaky = make_track("flaky")
    registry.failures[flaky.url] = [ProviderUnavailable("blip")]
    p, voice, _, hooks = build(registry=registry)
    await p.enqueue([flaky])
    await settle(40)
    assert p.current.title == "Track flaky" and p.state is PlayerState.PLAYING
    assert registry.stream_calls.count(flaky.url) == 2 and not hooks.errors
    await p.shutdown("test")


async def test_provider_outage_pauses_queue_after_repeated_failures():
    registry = FakeRegistry()
    tracks = [make_track(i) for i in range(5)]
    for tr in tracks:
        registry.failures[tr.url] = [ProviderUnavailable("down"), ProviderUnavailable("down")]
    p, voice, _, hooks = build(registry=registry)
    await p.enqueue(tracks)
    await settle(80)
    assert ("notify", "source.outage") in hooks.events
    assert p.state is PlayerState.IDLE
    assert len(p.queue.upcoming) == 1  # remaining track kept for when the source recovers
    assert not voice.played
    await p.shutdown("test")


async def test_stream_dying_immediately_invalidates_cache_and_skips():
    p, voice, registry, hooks = build()
    await p.enqueue([make_track(1), make_track(2)])
    await settle()
    voice.finish(RuntimeError("ffmpeg 403"))
    await settle()
    assert registry.invalidated == [make_track(1).url]
    assert p.current.title == "Track 2"
    await p.shutdown("test")


async def test_shutdown_is_idempotent_and_disconnects():
    p, voice, _, hooks = build()
    await p.enqueue([make_track(1)])
    await settle()
    await p.shutdown("stopped")
    await p.shutdown("stopped")
    assert hooks.ended == ["stopped"] and not voice.connected and p.closed
    with pytest.raises(RuntimeError):
        p.ensure_running()


async def test_idle_timeout_ends_session():
    p, voice, _, hooks = build(idle=0.05)
    await p.enqueue([make_track(1)])
    await settle()
    voice.finish()
    await asyncio.sleep(0.15)
    await settle()
    assert hooks.ended == ["idle"]
    assert ("notify", "session.idle_timeout") in hooks.events


async def test_enqueue_cancels_idle_timer():
    p, voice, _, hooks = build(idle=0.1)
    await settle()
    await asyncio.sleep(0.05)
    await p.enqueue([make_track(1)])
    await asyncio.sleep(0.1)
    await settle()
    assert hooks.ended == [] and p.state is PlayerState.PLAYING
    await p.shutdown("test")


async def test_empty_channel_pauses_then_leaves(monkeypatch):
    monkeypatch.setattr(player_mod, "EMPTY_CHANNEL_GRACE_SECONDS", 0.05)
    p, voice, _, hooks = build()
    await p.enqueue([make_track(1)])
    await settle()
    p.on_listeners_changed(0, stay_connected=False)
    await settle()
    assert p.state is PlayerState.PAUSED and p.auto_paused
    await asyncio.sleep(0.1)
    await settle()
    assert hooks.ended == ["empty_channel"]


async def test_someone_returning_resumes_auto_pause(monkeypatch):
    monkeypatch.setattr(player_mod, "EMPTY_CHANNEL_GRACE_SECONDS", 10)
    p, voice, _, hooks = build()
    await p.enqueue([make_track(1)])
    await settle()
    p.on_listeners_changed(0, stay_connected=True)
    await settle()
    assert p.state is PlayerState.PAUSED
    p.on_listeners_changed(2, stay_connected=True)
    await settle()
    assert p.state is PlayerState.PLAYING and hooks.ended == []
    await p.shutdown("test")


async def test_voice_lost_before_playing_ends_session():
    p, voice, _, hooks = build()
    voice.connected = False
    await p.enqueue([make_track(1)])
    await settle(40)
    assert hooks.ended == ["disconnected"]


async def test_volume_applies_live_and_is_clamped():
    p, voice, _, _ = build()
    await p.enqueue([make_track(1)])
    await settle()
    assert await p.set_volume(500) == 150
    assert voice.source.volume == 1.5
    await p.shutdown("test")


async def test_guilds_play_independently():
    a, voice_a, _, _ = build(1)
    b, voice_b, _, _ = build(2)
    await a.enqueue([make_track("a1"), make_track("a2")])
    await b.enqueue([make_track("b1")])
    await settle()
    await a.skip()
    await settle()
    assert a.current.title == "Track a2"
    assert b.current.title == "Track b1" and b.state is PlayerState.PLAYING
    await b.pause()
    assert a.state is PlayerState.PLAYING
    await a.shutdown("test")
    assert b.state is PlayerState.PAUSED and not b.closed
    await b.shutdown("test")


async def test_concurrent_commands_leave_consistent_state():
    p, voice, _, _ = build(max_queue=1000)
    await p.enqueue([make_track(i) for i in range(50)])
    await settle()

    async def churn(i):
        if i % 3 == 0:
            await p.enqueue([make_track(f"x{i}")])
        elif i % 3 == 1:
            try:
                await p.skip()
            except QueueError:
                pass
        else:
            try:
                await p.remove(1)
            except QueueError:
                pass

    await asyncio.gather(*(churn(i) for i in range(60)))
    await settle(100)
    # Exactly one track is current and playing; nothing is duplicated or lost into limbo.
    assert voice.is_playing() == (p.current is not None)
    ids = [t.entry_id for t in p.queue.upcoming]
    assert len(ids) == len(set(ids))
    assert p.current is None or p.current.entry_id not in ids
    await p.shutdown("test")


async def test_queue_limit_follows_tier_at_enqueue_time():
    limit = {"value": 2}
    hooks, registry = FakeHooks(), FakeRegistry()
    p = GuildPlayer(1, registry=registry, hooks=hooks, idle_timeout=lambda: None,  # type: ignore[arg-type]
                    max_queue=lambda: limit["value"], source_factory=FakeSource)
    p.attach(FakeVoice())
    assert await p.enqueue([make_track(i) for i in range(5)]) == 2
    await settle()
    limit["value"] = 10  # upgraded to premium mid-session
    assert await p.enqueue([make_track(i) for i in range(5, 10)]) == 5
    await p.shutdown("test")
