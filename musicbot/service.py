"""MusicService: the behaviour behind every command, button and legacy prefix.

Each method takes an `Actor` (who / where / which language / which settings),
enforces authorization server-side and returns a `Reply`, or raises
`UserError`/`PremiumRequired`. Transport (slash, button, prefix) is the cogs'
concern, so behaviour is identical everywhere.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import discord

from . import permissions as perms
from . import ui
from .errors import PremiumRequired, UserError
from .i18n import resolve_locale, t
from .models import Track
from .premium import Feature, Tier
from .queue import LoopMode, QueueError
from .sources import SourceError
from .store import GuildSettings
from .util import format_duration, truncate

if TYPE_CHECKING:
    from .bot import ShehrazadBot
    from .player import GuildPlayer

log = logging.getLogger(__name__)

PLAYLIST_NAME_MAX = 50


@dataclass
class Reply:
    embed: discord.Embed | None = None
    content: str | None = None
    view: discord.ui.View | None = None
    ephemeral: bool = False
    files: list[discord.File] = field(default_factory=list)


@dataclass
class Actor:
    guild: discord.Guild
    member: discord.Member
    channel_id: int
    locale: str
    settings: GuildSettings

    @property
    def voice_channel(self) -> discord.VoiceChannel | discord.StageChannel | None:
        # A member's voice state channel is always a voice or stage channel.
        state = self.member.voice
        return state.channel if state else None  # type: ignore[return-value]


class MusicService:
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot

    @property
    def manager(self):
        return self.bot.manager

    # ------------------------------------------------------------------ actors & checks

    async def actor(
        self, guild: discord.Guild, member: discord.Member, channel_id: int, user_locale: str | None
    ) -> Actor:
        settings = await self.bot.store.settings.get(guild.id)
        locale = resolve_locale(settings.language, user_locale or str(guild.preferred_locale))
        return Actor(guild, member, channel_id, locale, settings)

    def _player(self, actor: Actor) -> GuildPlayer | None:
        return self.manager.get(actor.guild.id)

    def _require_player(self, actor: Actor, *, need_current: bool = True) -> GuildPlayer:
        player = self._player(actor)
        if player is None or (need_current and player.current is None):
            raise UserError("player.nothing_playing", kind="info")
        return player

    def _require_same_voice(self, actor: Actor, player: GuildPlayer | None) -> None:
        member_channel = actor.voice_channel
        decision = perms.voice_check(
            member_channel.id if member_channel else None,
            player.voice_channel_id if player else None,
            bot_connected=bool(player and player.is_connected),
        )
        if not decision:
            raise UserError(decision.reason or "voice.join_first", kind="warning")

    def _require_control(self, actor: Actor, player: GuildPlayer) -> None:
        self._require_same_voice(actor, player)
        owns = bool(player.current and player.current.requester_id == actor.member.id)
        decision = perms.can_control(actor.member, actor.settings, owns_current=owns)
        if not decision:
            raise UserError(decision.reason or "perm.dj_only", kind="warning")

    def _require_manager(self, actor: Actor) -> None:
        if not perms.can_manage_settings(actor.member):
            raise UserError("perm.manage_guild", kind="warning")

    def _require_feature(self, actor: Actor, feature: Feature, flag: str | None = None) -> None:
        if flag and not self.bot.flags.enabled(flag):
            raise UserError("feature.disabled", kind="warning")
        if not self.bot.premium.has(actor.guild.id, feature):
            raise PremiumRequired(f"feature.{feature.value}")

    def _listeners(self, player: GuildPlayer) -> int:
        channel = getattr(player.voice, "channel", None) if player.voice else None
        members = getattr(channel, "members", [])
        return sum(1 for m in members if not m.bot)

    def _bot_voice_permissions(self, actor: Actor, channel: discord.abc.GuildChannel) -> list[str]:
        me = actor.guild.me
        return perms.missing(channel.permissions_for(me), perms.VOICE_PERMISSIONS)

    def _queue_error(self, actor: Actor, exc: QueueError) -> UserError:
        params = dict(exc.params)
        if exc.key == "queue.full" and self.bot.premium.tier(actor.guild.id) is Tier.FREE:
            return UserError("queue.full_upsell", kind="warning", **params)
        return UserError(exc.key, kind="warning", **params)

    # ------------------------------------------------------------------ playback

    async def ensure_session(self, actor: Actor) -> GuildPlayer:
        channel = actor.voice_channel
        if channel is None:
            raise UserError("voice.join_first", kind="warning")
        player = self._player(actor)
        if player is not None and player.is_connected and player.voice_channel_id != channel.id:
            if self._listeners(player) > 0:
                raise UserError("voice.same_channel", kind="warning")
        missing = self._bot_voice_permissions(actor, channel)
        if missing:
            raise UserError("perm.bot_missing", kind="warning", permissions=", ".join(missing), channel=channel.mention)
        player = await self.manager.get_or_create(actor.guild, actor.channel_id)
        await self.manager.connect(player, channel)
        return player

    async def play(self, actor: Actor, query: str, *, next_up: bool = False, shuffle: bool = False) -> Reply:
        decision = perms.can_request(actor.member, actor.settings)
        if not decision:
            raise UserError(decision.reason or "perm.requests_dj_only", kind="warning")
        if actor.voice_channel is None:
            raise UserError("voice.join_first", kind="warning")
        limits = self.bot.premium.limits(actor.guild.id)
        started = time.monotonic()
        try:
            result = await self.bot.registry.resolve(query, max_tracks=limits.max_playlist_import)
        except SourceError as exc:
            provider = "search" if not query.startswith("http") else "url"
            self.bot.metrics.source_errors.labels(provider, type(exc).__name__).inc()
            raise UserError(exc.key, kind="warning" if exc.transient else "error") from exc
        finally:
            self.bot.metrics.resolve_latency.labels("any", "query").observe(time.monotonic() - started)
        tracks = [tr.requested_by(actor.member.id) for tr in result.tracks]
        if shuffle and len(tracks) > 1:
            random.shuffle(tracks)
        return await self._enqueue(actor, tracks, next_up=next_up, playlist_title=result.playlist_title, truncated=result.truncated)

    async def _enqueue(
        self,
        actor: Actor,
        tracks: list[Track],
        *,
        next_up: bool = False,
        playlist_title: str | None = None,
        truncated: bool = False,
    ) -> Reply:
        if not tracks:
            raise UserError("source.no_results", kind="warning")
        player = await self.ensure_session(actor)
        was_idle = player.current is None
        position = 1 if next_up else None
        try:
            added = await player.enqueue(tracks, position=position)
        except QueueError as exc:
            raise self._queue_error(actor, exc) from exc
        loc = actor.locale
        if playlist_title is not None or len(tracks) > 1:
            text = t(loc, "play.queued_many", count=added, title=truncate(playlist_title or t(loc, "play.selection"), 80))
            if added < len(tracks) or truncated:
                limits = self.bot.premium.limits(actor.guild.id)
                key = "play.truncated_upsell" if self.bot.premium.tier(actor.guild.id) is Tier.FREE else "play.truncated"
                text += "\n" + t(loc, key, limit=min(limits.max_playlist_import, limits.max_queue))
            return Reply(embed=ui.success(text))
        track = tracks[0]
        if was_idle and len(player.queue.upcoming) <= 1:
            return Reply(embed=ui.success(t(loc, "play.starting", track=ui.track_link(track))))
        pos = 1 if next_up else len(player.queue.upcoming)
        return Reply(
            embed=ui.success(
                t(loc, "play.queued_one", track=ui.track_link(track), position=pos, duration=format_duration(track.duration))
            )
        )

    async def play_tracks(self, actor: Actor, tracks: list[Track], label: str, *, shuffle: bool = False) -> Reply:
        decision = perms.can_request(actor.member, actor.settings)
        if not decision:
            raise UserError(decision.reason or "perm.requests_dj_only", kind="warning")
        tracks = [tr.requested_by(actor.member.id) for tr in tracks]
        if shuffle:
            random.shuffle(tracks)
        return await self._enqueue(actor, tracks, playlist_title=label if len(tracks) > 1 else None)

    async def join(self, actor: Actor) -> Reply:
        player = await self.ensure_session(actor)
        channel = actor.voice_channel
        assert channel is not None
        player.text_channel_id = player.text_channel_id or actor.channel_id
        return Reply(embed=ui.success(t(actor.locale, "voice.joined", channel=channel.mention)))

    async def leave(self, actor: Actor) -> Reply:
        player = self._player(actor)
        if player is None:
            voice = actor.guild.voice_client
            if isinstance(voice, discord.VoiceClient) and voice.is_connected():
                await voice.disconnect(force=True)
                return Reply(embed=ui.success(t(actor.locale, "voice.left")))
            raise UserError("voice.not_connected", kind="info")
        self._require_control(actor, player) if player.current else self._require_same_voice(actor, player)
        await player.shutdown("stopped")
        return Reply(embed=ui.success(t(actor.locale, "voice.left")))

    async def pause(self, actor: Actor) -> Reply:
        player = self._require_player(actor)
        self._require_control(actor, player)
        if not await player.pause():
            raise UserError("player.already_paused", kind="info")
        return Reply(embed=ui.success(t(actor.locale, "player.paused")))

    async def resume(self, actor: Actor) -> Reply:
        player = self._require_player(actor)
        self._require_control(actor, player)
        if not await player.resume():
            raise UserError("player.not_paused", kind="info")
        return Reply(embed=ui.success(t(actor.locale, "player.resumed")))

    async def toggle_pause(self, actor: Actor) -> Reply:
        player = self._require_player(actor)
        from .player import PlayerState

        return await (self.resume(actor) if player.state is PlayerState.PAUSED else self.pause(actor))

    async def skip(self, actor: Actor) -> Reply:
        player = self._require_player(actor)
        self._require_same_voice(actor, player)
        current = player.current
        assert current is not None
        settings = actor.settings
        listeners = self._listeners(player)
        is_dj = perms.is_dj(actor.member, settings)
        owns = current.requester_id == actor.member.id
        immediate = is_dj or owns or listeners <= 1 or (not settings.vote_skip and not settings.dj_mode)
        loc = actor.locale
        if not immediate:
            if not settings.vote_skip:
                raise UserError("perm.dj_only", kind="warning")
            votes = player.add_skip_vote(actor.member.id)
            needed = perms.votes_needed(listeners, settings.vote_skip_ratio)
            if votes < needed:
                return Reply(embed=ui.info(None, t(loc, "skip.voted", votes=votes, needed=needed)))
            await player.skip()
            return Reply(embed=ui.success(t(loc, "skip.vote_passed", track=ui.track_link(current))))
        await player.skip()
        return Reply(embed=ui.success(t(loc, "skip.done", track=ui.track_link(current))))

    async def previous(self, actor: Actor) -> Reply:
        player = self._require_player(actor, need_current=False)
        self._require_control(actor, player)
        try:
            track = await player.previous()
        except QueueError as exc:
            raise self._queue_error(actor, exc) from exc
        return Reply(embed=ui.success(t(actor.locale, "player.previous", track=ui.track_link(track))))

    async def stop(self, actor: Actor) -> Reply:
        return await self.leave(actor)

    async def seek(self, actor: Actor, seconds: float) -> Reply:
        player = self._require_player(actor)
        self._require_control(actor, player)
        try:
            await player.seek(seconds)
        except QueueError as exc:
            if "duration" in exc.params:
                exc.params["duration"] = format_duration(float(exc.params["duration"]))  # type: ignore[arg-type]
            raise self._queue_error(actor, exc) from exc
        return Reply(embed=ui.success(t(actor.locale, "player.seeked", position=format_duration(seconds))))

    async def volume(self, actor: Actor, level: int | None) -> Reply:
        player = self._require_player(actor, need_current=False)
        if level is None:
            return Reply(embed=ui.info(None, t(actor.locale, "player.volume_is", level=round(player.volume * 100))), ephemeral=True)
        self._require_control(actor, player)
        applied = await player.set_volume(level)
        return Reply(embed=ui.success(t(actor.locale, "player.volume_set", level=applied)))

    async def nudge_volume(self, actor: Actor, delta: int) -> Reply:
        player = self._require_player(actor, need_current=False)
        return await self.volume(actor, round(player.volume * 100) + delta)

    async def loop(self, actor: Actor, mode: LoopMode | None) -> Reply:
        player = self._require_player(actor, need_current=False)
        self._require_control(actor, player)
        new_mode = mode or player.queue.loop.next()
        await player.set_loop(new_mode)
        return Reply(embed=ui.success(t(actor.locale, "loop.set", mode=f"{ui.loop_icon(new_mode)} {ui.loop_label(actor.locale, new_mode)}")))

    async def nowplaying(self, actor: Actor) -> Reply:
        player = self._require_player(actor)
        premium = self.bot.premium.status(actor.guild.id).is_premium
        show_art = self.bot.config.show_artwork and self.bot.registry.show_artwork(player.current)  # type: ignore[arg-type]
        embed = ui.now_playing_embed(player, actor.locale, show_artwork=show_art, premium=premium)
        view = None
        if player.panel_message_id and player.text_channel_id:
            view = discord.ui.View()
            url = f"https://discord.com/channels/{actor.guild.id}/{player.text_channel_id}/{player.panel_message_id}"
            view.add_item(ui.link_button(t(actor.locale, "btn.go_to_player"), url, "🎛️"))
        return Reply(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------ queue management

    def queue_reply(self, actor: Actor, page: int = 1) -> Reply:
        async def on_jump(interaction: discord.Interaction, position: int) -> None:
            reply_actor = await self.actor(actor.guild, interaction.user, interaction.channel_id or actor.channel_id, str(interaction.locale))  # type: ignore[arg-type]
            try:
                reply = await self.jump(reply_actor, position)
            except UserError as exc:
                await interaction.response.send_message(embed=self.error_embed(reply_actor.locale, exc), ephemeral=True)
                return
            await interaction.response.send_message(embed=reply.embed or ui.success(""), ephemeral=True)

        view = ui.QueueView(
            locale=actor.locale,
            get_player=lambda: self.manager.get(actor.guild.id),
            on_jump=on_jump,
            owner_id=actor.member.id,
            page=page,
        )
        return Reply(embed=view.render(), view=view)

    async def remove(self, actor: Actor, position: int) -> Reply:
        player = self._require_player(actor, need_current=False)
        self._require_same_voice(actor, player)
        try:
            target = player.queue.upcoming[position - 1] if 0 < position <= len(player.queue.upcoming) else None
            owns = bool(target and target.requester_id == actor.member.id)
            decision = perms.can_control(actor.member, actor.settings, owns_current=owns)
            if not decision:
                raise UserError(decision.reason or "perm.dj_only", kind="warning")
            track = await player.remove(position)
        except QueueError as exc:
            raise self._queue_error(actor, exc) from exc
        return Reply(embed=ui.success(t(actor.locale, "queue.removed", track=ui.track_link(track), position=position)))

    async def move(self, actor: Actor, source: int, target: int) -> Reply:
        player = self._require_player(actor, need_current=False)
        self._require_control(actor, player)
        try:
            track = await player.move(source, target)
        except QueueError as exc:
            raise self._queue_error(actor, exc) from exc
        return Reply(embed=ui.success(t(actor.locale, "queue.moved", track=ui.track_link(track), position=target)))

    async def clear(self, actor: Actor) -> Reply:
        player = self._require_player(actor, need_current=False)
        self._require_control(actor, player)
        count = await player.clear()
        return Reply(embed=ui.success(t(actor.locale, "queue.cleared", count=count)))

    async def shuffle(self, actor: Actor) -> Reply:
        player = self._require_player(actor, need_current=False)
        self._require_control(actor, player)
        try:
            await player.shuffle()
        except QueueError as exc:
            raise self._queue_error(actor, exc) from exc
        return Reply(embed=ui.success(t(actor.locale, "queue.shuffled", count=len(player.queue.upcoming))))

    async def jump(self, actor: Actor, position: int) -> Reply:
        player = self._require_player(actor, need_current=False)
        self._require_control(actor, player)
        try:
            track = await player.jump(position)
        except QueueError as exc:
            raise self._queue_error(actor, exc) from exc
        return Reply(embed=ui.success(t(actor.locale, "queue.jumped", track=ui.track_link(track))))

    async def restore(self, actor: Actor) -> Reply:
        self._require_feature(actor, Feature.RESUME_QUEUE)
        snapshot = await self.bot.store.snapshots.get(actor.guild.id)
        if not snapshot:
            raise UserError("restore.nothing", kind="info")
        from .queue import TrackQueue

        scratch = TrackQueue(max_size=self.bot.premium.limits(actor.guild.id).max_queue)
        scratch.restore(snapshot["payload"])
        tracks = ([scratch.current] if scratch.current else []) + scratch.upcoming
        reply = await self.play_tracks(actor, tracks, t(actor.locale, "restore.label"))
        return reply

    # ------------------------------------------------------------------ library

    def _validate_name(self, name: str) -> str:
        name = " ".join(name.split())
        if not name or len(name) > PLAYLIST_NAME_MAX:
            raise UserError("library.bad_name", kind="warning", max=PLAYLIST_NAME_MAX)
        return name

    async def library_save(self, actor: Actor, name: str) -> Reply:
        self._require_feature(actor, Feature.SAVED_PLAYLISTS, "feature.saved_playlists")
        name = self._validate_name(name)
        player = self._player(actor)
        tracks = []
        if player is not None:
            tracks = ([player.current] if player.current else []) + list(player.queue.upcoming)
        if not tracks:
            raise UserError("library.nothing_to_save", kind="info")
        limits = self.bot.premium.limits(actor.guild.id)
        repo = self.bot.store.playlists
        existing = await repo.find(actor.guild.id, name)
        if existing is None and await repo.count(actor.guild.id) >= limits.saved_playlists:
            raise UserError("library.limit", kind="warning", limit=limits.saved_playlists)
        if existing is not None and existing.created_by != actor.member.id and not perms.is_dj(actor.member, actor.settings):
            raise UserError("library.not_owner", kind="warning")
        tracks = tracks[: limits.playlist_tracks]
        await repo.save(actor.guild.id, name, actor.member.id, tracks)
        return Reply(embed=ui.success(t(actor.locale, "library.saved", name=name, count=len(tracks))))

    async def library_load(self, actor: Actor, name: str, *, shuffle: bool = False) -> Reply:
        self._require_feature(actor, Feature.SAVED_PLAYLISTS, "feature.saved_playlists")
        playlist = await self.bot.store.playlists.find(actor.guild.id, name)
        if playlist is None:
            raise UserError("library.not_found", kind="warning", name=truncate(name, 50))
        tracks = await self.bot.store.playlists.tracks(actor.guild.id, playlist.id)
        return await self.play_tracks(actor, tracks, playlist.name, shuffle=shuffle)

    async def library_list(self, actor: Actor) -> Reply:
        playlists = await self.bot.store.playlists.list_all(actor.guild.id)
        limits = self.bot.premium.limits(actor.guild.id)
        embed = ui.info(f"{ui.ICON['library']} {t(actor.locale, 'library.title')}")
        if not playlists:
            embed.description = t(actor.locale, "library.empty")
        else:
            embed.description = "\n".join(
                f"• **{ui.escape_markdown(p.name)}** — {t(actor.locale, 'library.track_count', count=p.track_count)}"
                for p in playlists
            )
        embed.set_footer(text=t(actor.locale, "library.usage", used=len(playlists), limit=limits.saved_playlists))
        return Reply(embed=embed)

    async def library_view(self, actor: Actor, name: str) -> Reply:
        playlist = await self.bot.store.playlists.find(actor.guild.id, name)
        if playlist is None:
            raise UserError("library.not_found", kind="warning", name=truncate(name, 50))
        tracks = await self.bot.store.playlists.tracks(actor.guild.id, playlist.id)
        lines = [f"`{i}.` {ui.track_link(tr, 60)}" for i, tr in enumerate(tracks[:20], start=1)]
        if len(tracks) > 20:
            lines.append(t(actor.locale, "panel.more_in_queue", count=len(tracks) - 20))
        embed = ui.info(f"{ui.ICON['library']} {ui.escape_markdown(playlist.name)}", "\n".join(lines) or "—")
        embed.set_footer(text=t(actor.locale, "library.track_count", count=len(tracks)))
        return Reply(embed=embed)

    async def library_delete(self, actor: Actor, name: str) -> Reply:
        playlist = await self.bot.store.playlists.find(actor.guild.id, name)
        if playlist is None:
            raise UserError("library.not_found", kind="warning", name=truncate(name, 50))
        if playlist.created_by != actor.member.id and not perms.is_dj(actor.member, actor.settings):
            raise UserError("library.not_owner", kind="warning")
        await self.bot.store.playlists.delete(actor.guild.id, playlist.id)
        return Reply(embed=ui.success(t(actor.locale, "library.deleted", name=playlist.name)))

    async def library_names(self, guild_id: int, prefix: str) -> list[str]:
        names = [p.name for p in await self.bot.store.playlists.list_all(guild_id)]
        prefix = prefix.lower()
        return [n for n in names if prefix in n.lower()][:25]

    async def favorite_current(self, actor: Actor) -> Reply:
        player = self._require_player(actor)
        track = player.current
        assert track is not None
        repo = self.bot.store.favorites
        limit = self.bot.premium.limits(actor.guild.id).favorites
        if await repo.count(actor.member.id) >= limit:
            raise UserError("favorites.limit", kind="warning", limit=limit)
        added = await repo.add(actor.member.id, track)
        key = "favorites.added" if added else "favorites.already"
        return Reply(embed=ui.success(t(actor.locale, key, track=ui.track_link(track))), ephemeral=True)

    async def favorites(self, actor: Actor) -> Reply:
        tracks = await self.bot.store.favorites.list_all(actor.member.id, limit=25)
        if not tracks:
            return Reply(embed=ui.info(None, t(actor.locale, "favorites.empty")), ephemeral=True)
        return self._picker(actor, tracks, "favorites.title")

    async def history(self, actor: Actor) -> Reply:
        limit = self.bot.premium.limits(actor.guild.id).history_shown
        tracks = await self.bot.store.history.recent(actor.guild.id, limit=limit)
        if not tracks:
            return Reply(embed=ui.info(None, t(actor.locale, "history.empty")), ephemeral=True)
        return self._picker(actor, tracks, "history.title")

    def _picker(self, actor: Actor, tracks: list[Track], title_key: str) -> Reply:
        lines = [f"`{i}.` {ui.track_link(tr, 60)} `{format_duration(tr.duration)}`" for i, tr in enumerate(tracks, start=1)]
        embed = ui.info(t(actor.locale, title_key), "\n".join(lines))

        async def on_pick(interaction: discord.Interaction, chosen: list[Track]) -> None:
            picker_actor = await self.actor(actor.guild, interaction.user, interaction.channel_id or actor.channel_id, str(interaction.locale))  # type: ignore[arg-type]
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                reply = await self.play_tracks(picker_actor, chosen, t(picker_actor.locale, title_key))
                await interaction.followup.send(embed=reply.embed or ui.success(""), ephemeral=True)
            except UserError as exc:
                await interaction.followup.send(embed=self.error_embed(picker_actor.locale, exc), ephemeral=True)

        view = ui.TrackPickerView(locale=actor.locale, tracks=tracks, on_pick=on_pick, owner_id=actor.member.id)
        return Reply(embed=embed, view=view, ephemeral=True)

    async def autocomplete_tracks(self, guild_id: int, user_id: int, current: str) -> list[tuple[str, str]]:
        if len(current.strip()) < 2:
            tracks = await self.bot.store.favorites.list_all(user_id, limit=10)
        else:
            tracks = await self.bot.store.history.search(guild_id, user_id, current, limit=10)
        return [(truncate(tr.title, 100), tr.url[:100]) for tr in tracks if len(tr.url) <= 100]

    # ------------------------------------------------------------------ rendering helpers

    def error_embed(self, locale: str, exc: UserError) -> discord.Embed:
        if isinstance(exc, PremiumRequired):
            return ui.premium_embed(locale, exc.feature_key)
        text = t(locale, exc.key, **exc.params)
        if exc.kind == "info":
            return ui.info(None, f"{ui.ICON['info']} {text}")
        if exc.kind == "warning":
            return ui.warning(text)
        return ui.error(text)

    def premium_view(self, locale: str, guild_id: int | None) -> discord.ui.View | None:
        """Upgrade button(s); only when sales are on and the guild isn't premium."""
        config = self.bot.config
        if not config.premium_configured or not self.bot.flags.enabled("premium.sales"):
            return None
        if self.bot.premium.status(guild_id).is_premium:
            return None
        view = discord.ui.View()
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.premium, sku_id=min(config.premium_sku_ids)))
        return view

    def panel_payload(self, actor: Actor) -> dict[str, Any]:
        return {"guild": actor.guild.id, "locale": actor.locale}
