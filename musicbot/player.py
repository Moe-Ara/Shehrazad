"""GuildPlayer: one independent playback session per guild.

Design:
  * A single long-running task per session (`_run`) owns playback transitions.
  * Commands mutate state under `self._lock`, record an intent in
    `self._control` and stop the current source; the loop then applies the
    intent. This keeps transitions predictable even when buttons and slash
    commands race each other.
  * discord.py calls `after` from its audio thread; we hop back onto the event
    loop with `call_soon_threadsafe` before touching any state.
  * Everything Discord-specific beyond the voice client (panels, history,
    snapshots) goes through `PlayerHooks`, so the player is testable with fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol

from .logging_setup import bind
from .models import StreamInfo, Track
from .queue import LoopMode, QueueError, TrackQueue
from .sources import ProviderRegistry, ProviderUnavailable, SourceError

log = logging.getLogger(__name__)

FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin"
FFMPEG_OPTIONS = '-vn -af "loudnorm=I=-16:TP=-1.5:LRA=11"'
MAX_CONSECUTIVE_FAILURES = 3
EMPTY_CHANNEL_GRACE_SECONDS = 120
OUTAGE_WAIT_SECONDS = 600


class PlayerState(StrEnum):
    IDLE = "idle"
    LOADING = "loading"
    PLAYING = "playing"
    PAUSED = "paused"
    ENDED = "ended"


class VoiceLike(Protocol):
    channel: Any
    source: Any

    def is_connected(self) -> bool: ...
    def is_playing(self) -> bool: ...
    def is_paused(self) -> bool: ...
    def play(self, source: Any, *, after: Callable[[Exception | None], Any]) -> None: ...
    def stop(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    async def disconnect(self, *, force: bool = False) -> None: ...


class PlayerHooks(Protocol):
    async def render_panel(self, player: GuildPlayer) -> None: ...
    async def notify(self, player: GuildPlayer, key: str, **params: Any) -> None: ...
    async def on_track_start(self, player: GuildPlayer, track: Track) -> None: ...
    async def on_track_error(self, player: GuildPlayer, track: Track, error: SourceError) -> None: ...
    async def on_session_end(self, player: GuildPlayer, reason: str) -> None: ...
    async def save_snapshot(self, player: GuildPlayer) -> None: ...


SourceFactory = Callable[[StreamInfo, float, float], Any]


def build_ffmpeg_source(stream: StreamInfo, seek: float, volume: float) -> Any:
    import discord

    before = FFMPEG_BEFORE
    if stream.is_live:
        before = before.replace("-reconnect_streamed 1", "-reconnect_streamed 1 -reconnect_at_eof 1")
    if seek > 0 and not stream.is_live:
        before = f"-ss {seek:.2f} {before}"
    if stream.headers:
        header_blob = "".join(f"{k}: {v}\r\n" for k, v in stream.headers.items())
        before = f"{before} -headers {shlex.quote(header_blob)}"
    return discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(stream.url, before_options=before, options=FFMPEG_OPTIONS),
        volume=volume,
    )


class GuildPlayer:
    def __init__(
        self,
        guild_id: int,
        *,
        registry: ProviderRegistry,
        hooks: PlayerHooks,
        idle_timeout: Callable[[], float | None],
        max_queue: Callable[[], int],
        source_factory: SourceFactory = build_ffmpeg_source,
        volume: float = 1.0,
        retry_delay: float = 2.0,
    ) -> None:
        self.guild_id = guild_id
        self.queue = TrackQueue(max_size=max_queue())
        self.voice: VoiceLike | None = None
        self.text_channel_id: int | None = None
        self.panel_message_id: int | None = None
        self.messages_since_panel = 0
        self.volume = volume
        self.state = PlayerState.IDLE
        self.last_error_key: str | None = None
        self.auto_paused = False

        self._registry = registry
        self._hooks = hooks
        self._idle_timeout = idle_timeout
        self._max_queue = max_queue
        self._source_factory = source_factory
        self._retry_delay = retry_delay

        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._track_done = asyncio.Event()
        self._control: str | None = None
        self._pending_seek = 0.0
        self._last_error: Exception | None = None
        self._started_at: float | None = None
        self._offset = 0.0
        self._skip_votes: set[int] = set()
        self._closed = False
        self._task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._empty_task: asyncio.Task[None] | None = None
        self._snapshot_task: asyncio.Task[None] | None = None
        self._loop = asyncio.get_running_loop()
        self.current_stream: StreamInfo | None = None

    # ------------------------------------------------------------------ status

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def current(self) -> Track | None:
        return self.queue.current

    @property
    def position(self) -> float:
        if self._started_at is None:
            return self._offset
        return self._offset + (self._loop.time() - self._started_at)

    @property
    def voice_channel_id(self) -> int | None:
        if self.voice is None or not self.voice.is_connected():
            return None
        channel = getattr(self.voice, "channel", None)
        return getattr(channel, "id", None)

    @property
    def is_connected(self) -> bool:
        return self.voice is not None and self.voice.is_connected()

    @property
    def skip_votes(self) -> int:
        return len(self._skip_votes)

    # ------------------------------------------------------------------ lifecycle

    def attach(self, voice: VoiceLike) -> None:
        self.voice = voice
        self.ensure_running()

    def ensure_running(self) -> None:
        if self._closed:
            raise RuntimeError("player is closed")
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"player-{self.guild_id}")

    async def shutdown(self, reason: str, *, disconnect: bool = True) -> None:
        """Idempotent teardown used by /stop, idle timeout, disconnects and shutdown."""
        if self._closed:
            return
        self._closed = True
        self._control = "stop"
        self.state = PlayerState.ENDED
        self._wake.set()
        for task in (self._idle_task, self._empty_task):
            if task is not None:
                task.cancel()
        if self.voice is not None and (self.voice.is_playing() or self.voice.is_paused()):
            self.voice.stop()
        self._track_done.set()
        if self._task is not None and self._task is not asyncio.current_task():
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
            if not self._task.done():
                self._task.cancel()
        if disconnect and self.voice is not None and self.voice.is_connected():
            with contextlib.suppress(Exception):
                await self.voice.disconnect(force=True)
        try:
            await self._hooks.on_session_end(self, reason)
        except Exception:
            log.exception("on_session_end hook failed")

    # ------------------------------------------------------------------ queue commands

    async def enqueue(self, tracks: list[Track], *, position: int | None = None) -> int:
        async with self._lock:
            self.queue.max_size = self._max_queue()
            added = self.queue.add(tracks, position=position)
        self._wake.set()
        self._cancel_idle()
        self._mark_dirty()
        return added

    async def remove(self, position: int) -> Track:
        async with self._lock:
            track = self.queue.remove(position)
        self._mark_dirty()
        return track

    async def move(self, source: int, target: int) -> Track:
        async with self._lock:
            track = self.queue.move(source, target)
        self._mark_dirty()
        return track

    async def clear(self) -> int:
        async with self._lock:
            count = self.queue.clear()
        self._mark_dirty()
        return count

    async def shuffle(self) -> None:
        async with self._lock:
            self.queue.shuffle()
        self._mark_dirty()

    async def set_loop(self, mode: LoopMode) -> LoopMode:
        async with self._lock:
            self.queue.loop = mode
            self.queue.version += 1
        self._mark_dirty()
        return mode

    # ------------------------------------------------------------------ playback commands

    def _interrupt(self, control: str) -> None:
        self._control = control
        if self.voice is not None and (self.voice.is_playing() or self.voice.is_paused()):
            self.voice.stop()  # -> after() -> _track_done
        elif self.state is not PlayerState.LOADING:
            self._track_done.set()
        self._wake.set()

    async def skip(self) -> Track | None:
        async with self._lock:
            track = self.queue.current
            if track is None:
                raise QueueError("player.nothing_playing")
            self._interrupt("skip")
        return track

    async def previous(self) -> Track:
        async with self._lock:
            track = self.queue.previous()
            self._interrupt("previous")
        return track

    async def jump(self, position: int) -> Track:
        async with self._lock:
            track = self.queue.jump(position)
            self._interrupt("jump")
        return track

    async def seek(self, seconds: float) -> float:
        async with self._lock:
            track = self.queue.current
            if track is None:
                raise QueueError("player.nothing_playing")
            duration = track.duration or (self.current_stream.duration if self.current_stream else None)
            if self.current_stream is not None and self.current_stream.is_live:
                raise QueueError("player.cannot_seek_live")
            if duration is not None and seconds >= duration:
                raise QueueError("player.seek_past_end", duration=duration)
            self._pending_seek = max(0.0, seconds)
            self._interrupt("seek")
        return seconds

    async def pause(self) -> bool:
        async with self._lock:
            if self.voice is None or not self.voice.is_playing() or self.voice.is_paused():
                return False
            self._offset = self.position
            self._started_at = None
            self.voice.pause()
            self.state = PlayerState.PAUSED
        self._mark_dirty()
        return True

    async def resume(self) -> bool:
        async with self._lock:
            self.auto_paused = False
            if self.voice is None or not self.voice.is_paused():
                return False
            self._started_at = self._loop.time()
            self.voice.resume()
            self.state = PlayerState.PLAYING
        self._mark_dirty()
        return True

    async def set_volume(self, percent: int) -> int:
        percent = max(1, min(150, int(percent)))
        self.volume = percent / 100
        source = getattr(self.voice, "source", None) if self.voice else None
        if source is not None and hasattr(source, "volume"):
            source.volume = self.volume
        self._mark_dirty()
        return percent

    def add_skip_vote(self, user_id: int) -> int:
        self._skip_votes.add(user_id)
        return len(self._skip_votes)

    # ------------------------------------------------------------------ voice events

    def on_listeners_changed(self, human_listeners: int, *, stay_connected: bool) -> None:
        """Called by the manager when people join/leave the bot's channel."""
        if human_listeners == 0:
            if self._empty_task is None or self._empty_task.done():
                self._empty_task = asyncio.create_task(self._empty_channel(stay_connected))
        else:
            if self._empty_task is not None:
                self._empty_task.cancel()
                self._empty_task = None
            if self.auto_paused:
                asyncio.create_task(self.resume())

    async def _empty_channel(self, stay_connected: bool) -> None:
        if self.voice is not None and self.voice.is_playing():
            if await self.pause():
                self.auto_paused = True
        if stay_connected:
            return  # 24/7: stay paused until someone comes back
        await asyncio.sleep(EMPTY_CHANNEL_GRACE_SECONDS)
        await self._hooks.notify(self, "session.left_empty")
        await self.shutdown("empty_channel")

    # ------------------------------------------------------------------ internals

    def _after(self, error: Exception | None) -> None:
        # Runs on discord.py's audio thread.
        self._loop.call_soon_threadsafe(self._on_track_done, error)

    def _on_track_done(self, error: Exception | None) -> None:
        self._last_error = error
        self._track_done.set()

    def _mark_dirty(self) -> None:
        if self._closed:
            return
        asyncio.create_task(self._safe_render())
        self._schedule_snapshot()

    async def _safe_render(self) -> None:
        try:
            await self._hooks.render_panel(self)
        except Exception:
            log.exception("panel render failed")

    def _schedule_snapshot(self) -> None:
        if self._snapshot_task is not None and not self._snapshot_task.done():
            return

        async def later() -> None:
            await asyncio.sleep(5)
            if not self._closed:
                try:
                    await self._hooks.save_snapshot(self)
                except Exception:
                    log.exception("snapshot save failed")

        self._snapshot_task = asyncio.create_task(later())

    def _schedule_idle(self) -> None:
        timeout = self._idle_timeout()
        if timeout is None or (self._idle_task is not None and not self._idle_task.done()):
            return

        async def expire() -> None:
            await asyncio.sleep(timeout)
            if self.state is PlayerState.IDLE and self.queue.is_empty and not self._closed:
                await self._hooks.notify(self, "session.idle_timeout")
                await self.shutdown("idle")

        self._idle_task = asyncio.create_task(expire())

    def _cancel_idle(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None

    async def _resolve_stream(self, track: Track) -> StreamInfo:
        try:
            return await self._registry.stream(track)
        except SourceError as exc:
            if not exc.transient:
                raise
            log.info("transient stream error, retrying once: %s", exc.detail)
            await asyncio.sleep(self._retry_delay)
            return await self._registry.stream(track)

    async def _run(self) -> None:
        with bind(guild_id=self.guild_id):
            try:
                await self._loop_body()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("player loop crashed")
                self.last_error_key = "error.internal"
                asyncio.create_task(self.shutdown("crash"))

    async def _loop_body(self) -> None:
        failures = 0
        while not self._closed:
            async with self._lock:
                track = self.queue.current or self.queue.start_next()
            if track is None:
                self.state = PlayerState.IDLE
                self._started_at, self._offset = None, 0.0
                self.current_stream = None
                self._mark_dirty()
                self._schedule_idle()
                self._wake.clear()
                if self.queue.is_empty and not self._closed:
                    await self._wake.wait()
                continue

            self._cancel_idle()
            seek, self._pending_seek = self._pending_seek, 0.0
            is_replay = seek > 0
            self.state = PlayerState.LOADING
            self._track_done.clear()
            self._last_error = None
            if not is_replay:
                self._mark_dirty()

            error: SourceError | None = None
            try:
                self.current_stream = await self._resolve_stream(track)
            except SourceError as exc:
                error = exc

            started = self._loop.time()
            if self._closed:
                break
            if error is None and self._control is None:
                if self.voice is None or not self.voice.is_connected():
                    asyncio.create_task(self.shutdown("disconnected", disconnect=False))
                    break
                assert self.current_stream is not None
                source = self._source_factory(self.current_stream, seek, self.volume)
                self._offset, self._started_at = seek, self._loop.time()
                self.voice.play(source, after=self._after)
                self.state = PlayerState.PLAYING
                self._skip_votes.clear()
                if not is_replay:
                    try:
                        await self._hooks.on_track_start(self, track)
                    except Exception:
                        log.exception("on_track_start hook failed")
                self._mark_dirty()
                await self._track_done.wait()

            played_for = self._loop.time() - started
            control, self._control = self._control, None
            stream_error = self._last_error

            if control == "stop" or self._closed:
                break
            if control in ("seek", "previous", "jump"):
                continue

            async with self._lock:
                if error is not None:
                    failures += 1 if error.transient else 0
                    self.last_error_key = error.key
                    await self._hooks.on_track_error(self, track, error)
                    self.queue.advance(skipped=True)
                elif stream_error is not None and played_for < 3 and control is None:
                    # ffmpeg died immediately: stream URL likely expired/blocked.
                    failures += 1
                    self._registry.invalidate(track)
                    await self._hooks.on_track_error(self, track, ProviderUnavailable(str(stream_error)))
                    self.queue.advance(skipped=True)
                else:
                    failures = 0
                    self.queue.advance(skipped=control == "skip")

                if failures >= MAX_CONSECUTIVE_FAILURES:
                    # Provider outage: keep the queue, stop hammering the provider
                    # and wait for a user action (or give up after a while).
                    failures = 0
                    await self._hooks.notify(self, "source.outage")
                    self.state = PlayerState.IDLE
                    self._wake.clear()
                    paused_queue = True
                else:
                    paused_queue = False
            self._schedule_snapshot()
            if paused_queue:
                self._mark_dirty()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=OUTAGE_WAIT_SECONDS)
                except TimeoutError:
                    asyncio.create_task(self.shutdown("outage"))
                    break


Connector = Callable[[Any], Awaitable[VoiceLike]]
