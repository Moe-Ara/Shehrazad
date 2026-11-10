"""ShehrazadBot: wiring, lifecycle, events and background jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from . import __version__, ui
from .config import Settings
from .db import Database
from .health import HealthServer
from .i18n import resolve_locale, t
from .manager import PlayerManager
from .ops import FeatureFlags, Metrics, RateLimiter
from .premium import EntitlementService
from .service import MusicService
from .sources import DirectProvider, ProviderRegistry, YouTubeProvider
from .store import (
    AuditRepo,
    DataRights,
    EntitlementRepo,
    FavoritesRepo,
    FeedbackRepo,
    FlagRepo,
    GrantRepo,
    HistoryRepo,
    KVRepo,
    PlaylistRepo,
    PresenceRepo,
    SettingsRepo,
    SnapshotRepo,
)

log = logging.getLogger(__name__)

COGS = (
    "musicbot.cogs.music",
    "musicbot.cogs.library",
    "musicbot.cogs.settings",
    "musicbot.cogs.premium",
    "musicbot.cogs.info",
)


@dataclass
class Store:
    settings: SettingsRepo
    playlists: PlaylistRepo
    favorites: FavoritesRepo
    history: HistoryRepo
    snapshots: SnapshotRepo
    entitlements: EntitlementRepo
    grants: GrantRepo
    audit: AuditRepo
    flags: FlagRepo
    kv: KVRepo
    feedback: FeedbackRepo
    presence: PresenceRepo
    rights: DataRights

    @classmethod
    def create(cls, db: Database) -> Store:
        return cls(
            settings=SettingsRepo(db), playlists=PlaylistRepo(db), favorites=FavoritesRepo(db),
            history=HistoryRepo(db), snapshots=SnapshotRepo(db), entitlements=EntitlementRepo(db),
            grants=GrantRepo(db), audit=AuditRepo(db), flags=FlagRepo(db), kv=KVRepo(db),
            feedback=FeedbackRepo(db), presence=PresenceRepo(db), rights=DataRights(db),
        )


def build_mentions(commands_: list[app_commands.AppCommand]) -> dict[str, str]:
    """Map "name" / "group sub" to clickable </name:id> mentions."""
    mentions: dict[str, str] = {}
    for command in commands_:
        mentions[command.name] = command.mention
        for option in command.options:
            if isinstance(option, app_commands.AppCommandGroup):
                mentions[f"{command.name} {option.name}"] = f"</{command.name} {option.name}:{command.id}>"
    return mentions


class Tree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot: ShehrazadBot = interaction.client  # type: ignore[assignment]
        if interaction.entitlements:
            asyncio.create_task(bot.observe_entitlements(interaction.entitlements))
        if interaction.type is discord.InteractionType.autocomplete:
            return True
        if not bot.limiter.allow(interaction.user.id):
            bot.metrics.rate_limited.inc()
            locale = resolve_locale("auto", str(interaction.locale))
            await interaction.response.send_message(embed=ui.warning(t(locale, "error.rate_limited")), ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        locale = resolve_locale("auto", str(interaction.locale))
        if isinstance(error, app_commands.CheckFailure):
            return  # interaction_check already answered
        log.error("unhandled app command error", exc_info=error)
        embed = ui.error(t(locale, "error.internal", ref="-"))
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass


class ShehrazadBot(commands.Bot):
    def __init__(self, config: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        intents.guild_messages = True  # message events (no content) keep the panel near the bottom
        # Slash commands only: no Message Content intent, no prefix commands. commands.Bot needs a
        # prefix argument, but on_message never processes commands, so it is never used.
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            tree_cls=Tree,
            help_command=None,
            max_messages=None,
            chunk_guilds_at_startup=False,
            activity=discord.Activity(type=discord.ActivityType.listening, name="/play • /help"),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.version = __version__
        self.metrics = Metrics()
        self.limiter = RateLimiter(capacity=8, refill_per_second=0.8)
        self.feedback_limiter = RateLimiter(capacity=2, refill_per_second=1 / 600)
        self.db = Database(config.database_path)
        self.store = Store.create(self.db)
        static_disabled = frozenset(f"provider.{p}" for p in {"youtube", "direct"} - set(config.enabled_providers))
        self.flags = FeatureFlags(self.store.flags, static_disabled)
        self.premium = EntitlementService(
            self.store.entitlements, self.store.grants, self.store.audit, sku_ids=config.premium_sku_ids
        )
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="extract")
        self.registry = ProviderRegistry(
            [
                YouTubeProvider(self.executor),
                DirectProvider(self.executor, allow_any_url=config.allow_direct_urls),
            ],
            is_enabled=lambda name: self.flags.enabled(f"provider.{name}"),
        )
        self.service = MusicService(self)
        self.health = HealthServer(self, config.health_host, config.health_port)
        self.gateway_down_since: float | None = None
        self.started_monotonic = time.monotonic()
        self.command_mentions: dict[str, str] = {}
        self._background: list[asyncio.Task[None]] = []
        self._first_ready = True

    # ------------------------------------------------------------------ setup

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.flags.refresh()
        await self.premium.load()
        self.manager = PlayerManager(
            self,
            config=self.config,
            registry=self.registry,
            settings=self.store.settings,
            history=self.store.history,
            snapshots=self.store.snapshots,
            premium=self.premium,
            flags=self.flags,
            metrics=self.metrics,
        )
        for name in COGS:
            await self.load_extension(name)
        from .cogs.music import Music

        music = self.get_cog("Music")
        assert isinstance(music, Music)
        handler = music.handle_panel
        self.manager.panel_handler = handler
        self.add_view(ui.PanelView(handler))  # route clicks on panels sent before a restart
        self.manager.start()
        await self.health.start()
        self._spawn(self._every(1800, self.reconcile_entitlements, initial_delay=30), "reconcile")
        self._spawn(self._every(60, self._refresh_runtime_state), "refresh")
        self._spawn(self._every(86400, self.purge_expired_data, initial_delay=300), "purge")
        if self.config.sync_commands:
            await self.sync_commands()

    def _spawn(self, coro: Awaitable[None], name: str) -> None:
        self._background.append(asyncio.create_task(coro, name=name))  # type: ignore[arg-type]

    async def _every(self, seconds: float, job: Callable[[], Awaitable[object]], *, initial_delay: float = 0) -> None:
        await asyncio.sleep(initial_delay)
        while True:
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("background job %s failed", getattr(job, "__name__", job))
            await asyncio.sleep(seconds)

    async def sync_commands(self) -> None:
        """Sync slash commands only when their definitions actually changed."""
        payload = json.dumps([c.to_dict(self.tree) for c in self.tree.get_commands()], sort_keys=True, default=str)
        scope = f"guild:{self.config.dev_guild_id}" if self.config.dev_guild_id else "global"
        digest = hashlib.sha256(payload.encode()).hexdigest()
        key = f"commands_hash:{scope}"
        guild = discord.Object(id=self.config.dev_guild_id) if self.config.dev_guild_id else None
        if await self.store.kv.get(key) == digest:
            log.info("slash commands unchanged; skipping sync")
            synced = await self.tree.fetch_commands(guild=guild)
        else:
            if guild is not None:
                self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            await self.store.kv.set(key, digest)
            log.info("synced %d slash commands (%s)", len(synced), scope)
        self.command_mentions = build_mentions(synced)

    # ------------------------------------------------------------------ jobs

    async def observe_entitlements(self, entitlements: list[discord.Entitlement]) -> None:
        try:
            changed = await self.premium.apply_many(entitlements, "interaction")
            self.metrics.entitlement_events.labels("interaction", str(bool(changed))).inc()
        except Exception:
            log.exception("failed to apply interaction entitlements")

    async def reconcile_entitlements(self) -> None:
        if not self.config.premium_configured:
            return
        skus = [discord.Object(id=s) for s in self.config.premium_sku_ids]
        ok = await self.premium.reconcile(
            lambda: self.entitlements(limit=None, skus=skus, exclude_ended=False, exclude_deleted=True)
        )
        if not ok:
            self.metrics.reconcile_failures.inc()
        self.metrics.premium_guilds.set(len(self.premium.premium_guild_ids()))

    async def _refresh_runtime_state(self) -> None:
        await self.flags.refresh()
        await self.premium.refresh_grants()
        self.metrics.premium_guilds.set(len(self.premium.premium_guild_ids()))
        self.metrics.guilds.set(len(self.guilds))

    async def purge_expired_data(self) -> None:
        counts = await self.store.rights.purge(
            history_days=self.config.history_retention_days,
            feedback_days=self.config.feedback_retention_days,
            departed_guild_days=self.config.departed_guild_retention_days,
        )
        log.info("retention purge complete", extra={"counts": counts})

    # ------------------------------------------------------------------ events

    async def on_ready(self) -> None:
        self.gateway_down_since = None
        self.metrics.gateway_connected.set(1)
        self.metrics.guilds.set(len(self.guilds))
        log.info("ready as %s in %d guilds (v%s)", self.user, len(self.guilds), self.version)
        if self._first_ready:
            self._first_ready = False
            for guild in self.guilds:
                await self.store.presence.joined(guild.id)
            restored = await self.manager.restore_stay_connected()
            if restored:
                log.info("restored %d 24/7 sessions", restored)

    async def on_disconnect(self) -> None:
        if self.gateway_down_since is None:
            self.gateway_down_since = time.monotonic()
        self.metrics.gateway_connected.set(0)

    async def on_resumed(self) -> None:
        self.gateway_down_since = None
        self.metrics.gateway_connected.set(1)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.store.presence.joined(guild.id)
        self.metrics.guilds.set(len(self.guilds))
        log.info("joined guild", extra={"guild_id": guild.id, "members": guild.member_count})

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.store.presence.left(guild.id)
        player = self.manager.get(guild.id)
        if player is not None:
            await player.shutdown("removed", disconnect=False)
        self.store.settings.forget(guild.id)
        self.metrics.guilds.set(len(self.guilds))
        log.info("removed from guild; data kept for %d days", self.config.departed_guild_retention_days, extra={"guild_id": guild.id})

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        await self.manager.on_voice_state_update(member, before, after)

    async def on_message(self, message: discord.Message) -> None:
        # Only keeps the player panel near the bottom of busy channels. Never runs commands:
        # the bot answers slash commands (and its own buttons) only.
        self.manager.note_message(message)

    async def on_entitlement_create(self, entitlement: discord.Entitlement) -> None:
        changed = await self.premium.apply(entitlement, "gateway")
        self.metrics.entitlement_events.labels("gateway", str(changed)).inc()

    async def on_entitlement_update(self, entitlement: discord.Entitlement) -> None:
        changed = await self.premium.apply(entitlement, "gateway")
        self.metrics.entitlement_events.labels("gateway", str(changed)).inc()

    async def on_entitlement_delete(self, entitlement: discord.Entitlement) -> None:
        changed = await self.premium.apply_deleted(entitlement)
        self.metrics.entitlement_events.labels("gateway_delete", str(changed)).inc()

    # ------------------------------------------------------------------ shutdown

    async def close(self) -> None:
        log.info("shutting down")
        for task in self._background:
            task.cancel()
        if hasattr(self, "manager"):
            try:
                await asyncio.wait_for(self.manager.shutdown_all(), timeout=15)
            except Exception:
                log.exception("error while stopping sessions")
        await self.health.stop()
        await super().close()
        await self.db.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
