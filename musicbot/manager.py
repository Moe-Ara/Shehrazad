"""PlayerManager: guild -> session registry, voice events, panels, persistence.

Implements `PlayerHooks` for every GuildPlayer, so all Discord message I/O
for sessions (the persistent Now Playing panel and short notices) lives here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord

from . import ui
from .config import Settings
from .errors import UserError
from .i18n import resolve_locale, t
from .models import Track
from .ops import FeatureFlags, Metrics
from .player import GuildPlayer, PlayerState
from .premium import EntitlementService, Feature
from .sources import ProviderRegistry, SourceError
from .store import GuildSettings, HistoryRepo, SettingsRepo, SnapshotRepo
from .util import truncate

if TYPE_CHECKING:
    from .bot import ShehrazadBot

log = logging.getLogger(__name__)

PANEL_MIN_INTERVAL = 1.5  # seconds between panel edits per guild
PANEL_BURY_THRESHOLD = 8  # newer messages before the panel is re-posted at the bottom
PROGRESS_REFRESH_SECONDS = 20
NOTICE_TTL_SECONDS = 20


@dataclass
class _RenderState:
    last: float = 0.0
    dirty: bool = False
    task: asyncio.Task[None] | None = None


class PlayerManager:
    def __init__(
        self,
        bot: ShehrazadBot,
        *,
        config: Settings,
        registry: ProviderRegistry,
        settings: SettingsRepo,
        history: HistoryRepo,
        snapshots: SnapshotRepo,
        premium: EntitlementService,
        flags: FeatureFlags,
        metrics: Metrics,
    ) -> None:
        self.bot = bot
        self.config = config
        self.registry = registry
        self.settings = settings
        self.history = history
        self.snapshots = snapshots
        self.premium = premium
        self.flags = flags
        self.metrics = metrics
        self.players: dict[int, GuildPlayer] = {}
        self._create_lock = asyncio.Lock()
        self._render: dict[int, _RenderState] = {}
        self._progress_task: asyncio.Task[None] | None = None
        self.panel_handler: ui.PanelHandler | None = None

    # ------------------------------------------------------------------ registry

    def get(self, guild_id: int) -> GuildPlayer | None:
        player = self.players.get(guild_id)
        return player if player is not None and not player.closed else None

    @property
    def active_count(self) -> int:
        return sum(1 for p in self.players.values() if not p.closed)

    def _update_gauges(self) -> None:
        self.metrics.sessions.set(self.active_count)
        self.metrics.playing.set(sum(1 for p in self.players.values() if p.state is PlayerState.PLAYING))

    def stay_connected(self, guild_id: int, settings: GuildSettings) -> bool:
        return (
            settings.stay_connected
            and self.premium.has(guild_id, Feature.STAY_CONNECTED)
            and self.flags.enabled("feature.stay_connected")
        )

    def idle_timeout(self, guild_id: int) -> float | None:
        settings = self.settings._cache.get(guild_id) or GuildSettings(guild_id=guild_id)
        if self.stay_connected(guild_id, settings):
            return None
        limits = self.premium.limits(guild_id)
        requested = settings.idle_timeout_seconds or limits.default_idle_seconds
        return float(min(requested, limits.max_idle_seconds))

    async def get_or_create(self, guild: discord.Guild, text_channel_id: int | None) -> GuildPlayer:
        async with self._create_lock:
            existing = self.get(guild.id)
            if existing is not None:
                return existing
            active = self.active_count
            priority = self.premium.has(guild.id, Feature.PRIORITY_CAPACITY)
            if active >= self.config.max_sessions_hard or (active >= self.config.max_sessions_soft and not priority):
                raise UserError("capacity.full", kind="warning")
            settings = await self.settings.get(guild.id)
            player = GuildPlayer(
                guild.id,
                registry=self.registry,
                hooks=self,
                idle_timeout=lambda: self.idle_timeout(guild.id),
                max_queue=lambda: self.premium.limits(guild.id).max_queue,
                volume=settings.default_volume / 100,
            )
            player.text_channel_id = settings.music_channel_id or text_channel_id
            self.players[guild.id] = player
            self._update_gauges()
            log.info("session created", extra={"guild_id": guild.id, "active_sessions": self.active_count})
            return player

    async def connect(self, player: GuildPlayer, channel: discord.VoiceChannel | discord.StageChannel) -> None:
        voice = channel.guild.voice_client
        if isinstance(voice, discord.VoiceClient) and voice.is_connected():
            if voice.channel is None or voice.channel.id != channel.id:
                await voice.move_to(channel)
            if player.voice is not voice:
                player.attach(voice)  # type: ignore[arg-type]
            else:
                player.ensure_running()
            return
        try:
            voice = await channel.connect(timeout=20, reconnect=True, self_deaf=True)
        except (TimeoutError, discord.ClientException) as exc:
            log.warning("voice connect failed: %s", exc)
            raise UserError("voice.connect_failed") from exc
        player.attach(voice)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ voice & message events

    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        guild = member.guild
        player = self.get(guild.id)
        if player is None:
            return
        if self.bot.user is not None and member.id == self.bot.user.id:
            if after.channel is None:
                # Give discord.py's own reconnect logic a moment before giving up.
                await asyncio.sleep(2)
                voice = guild.voice_client
                if not isinstance(voice, discord.VoiceClient) or not voice.is_connected():
                    await self._persist(player)
                    await player.shutdown("disconnected", disconnect=False)
            return
        channel_id = player.voice_channel_id
        if channel_id is None or channel_id not in (
            getattr(before.channel, "id", None), getattr(after.channel, "id", None)
        ):
            return
        voice = guild.voice_client
        channel = getattr(voice, "channel", None)
        if channel is None:
            return
        humans = sum(1 for m in channel.members if not m.bot)
        settings = await self.settings.get(guild.id)
        player.on_listeners_changed(humans, stay_connected=self.stay_connected(guild.id, settings))

    def note_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        player = self.get(message.guild.id)
        if player is not None and message.channel.id == player.text_channel_id and message.id != player.panel_message_id:
            player.messages_since_panel += 1

    # ------------------------------------------------------------------ hooks: panel

    async def _locale(self, guild_id: int) -> str:
        settings = await self.settings.get(guild_id)
        guild = self.bot.get_guild(guild_id)
        return resolve_locale(settings.language, str(guild.preferred_locale) if guild else None)

    async def render_panel(self, player: GuildPlayer) -> None:
        state = self._render.setdefault(player.guild_id, _RenderState())
        state.dirty = True
        if state.task is not None and not state.task.done():
            return
        state.task = asyncio.create_task(self._render_loop(player, state))

    async def _render_loop(self, player: GuildPlayer, state: _RenderState) -> None:
        while state.dirty:
            wait = state.last + PANEL_MIN_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            state.dirty = False
            try:
                await self._render_now(player)
            except Exception:
                log.exception("panel render failed", extra={"guild_id": player.guild_id})
            state.last = time.monotonic()

    def _channel(self, channel_id: int | None) -> discord.abc.Messageable | None:
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _render_now(self, player: GuildPlayer) -> None:
        channel = self._channel(player.text_channel_id)
        if channel is None or self.panel_handler is None:
            return
        locale = await self._locale(player.guild_id)
        premium = self.premium.status(player.guild_id).is_premium
        show_art = self.config.show_artwork and player.current is not None and self.registry.show_artwork(player.current)
        embed = ui.now_playing_embed(player, locale, show_artwork=show_art, premium=premium)
        view = ui.PanelView(self.panel_handler, locale=locale, player=player)
        ended = player.state is PlayerState.ENDED
        if player.panel_message_id is not None and (ended or player.messages_since_panel <= PANEL_BURY_THRESHOLD):
            message = channel.get_partial_message(player.panel_message_id)  # type: ignore[attr-defined]
            try:
                await message.edit(embed=embed, view=None if ended else view)
                return
            except discord.NotFound:
                player.panel_message_id = None
            except discord.Forbidden:
                log.warning("cannot edit panel (missing permissions)", extra={"guild_id": player.guild_id})
                return
        if ended:
            return
        old_id = player.panel_message_id
        try:
            message = await channel.send(embed=embed, view=view)
        except discord.Forbidden:
            log.warning("cannot post panel (missing permissions)", extra={"guild_id": player.guild_id})
            return
        player.panel_message_id = message.id
        player.messages_since_panel = 0
        if old_id is not None:
            with contextlib.suppress(discord.HTTPException):
                await channel.get_partial_message(old_id).delete()  # type: ignore[attr-defined]

    async def notify(self, player: GuildPlayer, key: str, **params: Any) -> None:
        channel = self._channel(player.text_channel_id)
        if channel is None:
            return
        locale = await self._locale(player.guild_id)
        with contextlib.suppress(discord.HTTPException):
            await channel.send(embed=ui.warning(t(locale, key, **params)), delete_after=NOTICE_TTL_SECONDS)

    # ------------------------------------------------------------------ hooks: lifecycle

    async def on_track_start(self, player: GuildPlayer, track: Track) -> None:
        self.metrics.tracks_started.labels(track.provider).inc()
        self._update_gauges()
        try:
            await self.history.record(player.guild_id, track)
        except Exception:
            log.exception("failed to record history")

    async def on_track_error(self, player: GuildPlayer, track: Track, error: SourceError) -> None:
        kind = "transient" if error.transient else type(error).__name__
        self.metrics.source_errors.labels(track.provider, kind).inc()
        log.info("track failed: %s", error.detail or error.key, extra={"provider": track.provider, "kind": kind})
        locale = await self._locale(player.guild_id)
        await self.notify(
            player, "player.track_failed", title=truncate(track.title, 80), reason=t(locale, error.key)
        )

    async def on_session_end(self, player: GuildPlayer, reason: str) -> None:
        log.info("session ended", extra={"guild_id": player.guild_id, "reason": reason})
        if reason == "stopped":
            with contextlib.suppress(Exception):
                await self.snapshots.delete(player.guild_id)
        await self._render_now(player)
        self._render.pop(player.guild_id, None)
        if self.players.get(player.guild_id) is player:
            del self.players[player.guild_id]
        self._update_gauges()

    async def save_snapshot(self, player: GuildPlayer) -> None:
        if not self.premium.has(player.guild_id, Feature.RESUME_QUEUE):
            return
        if player.queue.is_empty:
            await self.snapshots.delete(player.guild_id)
            return
        await self.snapshots.save(
            player.guild_id, player.voice_channel_id, player.text_channel_id, player.queue.snapshot()
        )

    async def _persist(self, player: GuildPlayer) -> None:
        try:
            await self.save_snapshot(player)
        except Exception:
            log.exception("snapshot failed")

    # ------------------------------------------------------------------ background

    def start(self) -> None:
        if self._progress_task is None:
            self._progress_task = asyncio.create_task(self._progress_loop())

    async def _progress_loop(self) -> None:
        while True:
            await asyncio.sleep(PROGRESS_REFRESH_SECONDS)
            for player in list(self.players.values()):
                if player.state is PlayerState.PLAYING:
                    await self.render_panel(player)
            self._update_gauges()

    async def restore_stay_connected(self) -> int:
        """After a restart, rejoin 24/7 premium guilds and resume their queues."""
        restored = 0
        for guild_id in await self.snapshots.all():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            settings = await self.settings.get(guild_id)
            if not self.stay_connected(guild_id, settings):
                continue
            snapshot = await self.snapshots.get(guild_id)
            channel_id = settings.stay_channel_id or (snapshot or {}).get("voice_channel_id")
            channel = guild.get_channel(channel_id) if channel_id else None
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                continue
            try:
                player = await self.get_or_create(guild, (snapshot or {}).get("text_channel_id"))
                await self.connect(player, channel)
                if snapshot:
                    tracks = []
                    from .queue import TrackQueue

                    scratch = TrackQueue(max_size=self.premium.limits(guild_id).max_queue)
                    scratch.restore(snapshot["payload"])
                    if scratch.current:
                        tracks.append(scratch.current)
                    tracks.extend(scratch.upcoming)
                    player.queue.loop = scratch.loop
                    await player.enqueue(tracks)
                restored += 1
            except Exception:
                log.exception("failed to restore 24/7 session", extra={"guild_id": guild_id})
        return restored

    async def shutdown_all(self) -> None:
        if self._progress_task is not None:
            self._progress_task.cancel()
        for player in list(self.players.values()):
            await self._persist(player)
            await player.shutdown("shutdown")
