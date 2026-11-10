"""Embeds and views: one visual language for every response.

Brand: indigo primary, semantic success/warning/error colours, the same emoji
per action everywhere, short mobile-friendly embeds, and text labels on every
button (emoji alone is not accessible).
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord

from .i18n import t
from .models import Track
from .queue import LoopMode
from .util import escape_markdown, format_duration, truncate

if TYPE_CHECKING:
    from .player import GuildPlayer

BRAND_NAME = "Shehrazad"
COLOR_PRIMARY = 0x6C5CE7
COLOR_SUCCESS = 0x2ECC71
COLOR_WARNING = 0xF1C40F
COLOR_ERROR = 0xE74C3C
COLOR_PREMIUM = 0xF5B942
COLOR_NEUTRAL = 0x2B2D31

ICON = {
    "play": "▶️", "pause": "⏸️", "skip": "⏭️", "previous": "⏮️", "stop": "⏹️",
    "loop_off": "➡️", "loop_track": "🔂", "loop_queue": "🔁", "shuffle": "🔀",
    "vol_down": "🔉", "vol_up": "🔊", "queue": "📜", "favorite": "❤️",
    "success": "✅", "error": "⚠️", "info": "ℹ️", "premium": "✨", "loading": "⏳",
    "music": "🎶", "settings": "⚙️", "library": "📚",
}

PANEL_PREFIX = "shz:panel:"


def track_link(track: Track, limit: int = 80) -> str:
    title = escape_markdown(truncate(track.title, limit))
    return f"[{title}]({track.url})" if track.url.startswith("http") else title


def progress_bar(position: float, duration: float | None, width: int = 14) -> str:
    if not duration or duration <= 0:
        return f"`{format_duration(position)}`"
    ratio = max(0.0, min(1.0, position / duration))
    filled = round(ratio * width)
    bar = "▰" * filled + "▱" * (width - filled)
    return f"{bar} `{format_duration(position)} / {format_duration(duration)}`"


def _base(title: str | None, description: str | None, color: int) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


def info(title: str | None, description: str | None = None) -> discord.Embed:
    return _base(title, description, COLOR_PRIMARY)


def success(description: str, title: str | None = None) -> discord.Embed:
    return _base(title, f"{ICON['success']} {description}", COLOR_SUCCESS)


def warning(description: str, title: str | None = None) -> discord.Embed:
    return _base(title, f"{ICON['error']} {description}", COLOR_WARNING)


def error(description: str, title: str | None = None) -> discord.Embed:
    return _base(title, f"{ICON['error']} {description}", COLOR_ERROR)


def premium_embed(locale: str, feature_key: str) -> discord.Embed:
    embed = _base(
        f"{ICON['premium']} {t(locale, 'premium.gate_title')}",
        t(locale, "premium.gate_body", feature=t(locale, feature_key)),
        COLOR_PREMIUM,
    )
    return embed


def loop_label(locale: str, mode: LoopMode) -> str:
    return t(locale, f"loop.{mode.value}")


def loop_icon(mode: LoopMode) -> str:
    return ICON[f"loop_{mode.value}"]


# --------------------------------------------------------------------------- now playing


def now_playing_embed(player: GuildPlayer, locale: str, *, show_artwork: bool, premium: bool) -> discord.Embed:
    from .player import PlayerState

    track = player.current
    state = player.state
    if track is None or state in (PlayerState.IDLE, PlayerState.ENDED):
        title = t(locale, "panel.ended_title") if state is PlayerState.ENDED else t(locale, "panel.idle_title")
        body = t(locale, "panel.ended_body") if state is PlayerState.ENDED else t(locale, "panel.idle_body")
        embed = _base(f"{ICON['music']} {title}", body, COLOR_NEUTRAL)
        if player.queue.upcoming:
            embed.add_field(name=t(locale, "panel.up_next"), value=track_link(player.queue.upcoming[0], 60), inline=False)
        return embed

    status = {
        PlayerState.LOADING: f"{ICON['loading']} {t(locale, 'panel.loading')}",
        PlayerState.PLAYING: f"{ICON['play']} {t(locale, 'panel.playing')}",
        PlayerState.PAUSED: f"{ICON['pause']} {t(locale, 'panel.paused')}",
    }.get(state, "")
    duration = track.duration or (player.current_stream.duration if player.current_stream else None)
    is_live = bool(player.current_stream and player.current_stream.is_live)
    embed = _base(None, None, COLOR_PRIMARY)
    embed.set_author(name=status)
    embed.description = f"### {track_link(track, 90)}"
    if track.artist:
        embed.description += f"\n{escape_markdown(truncate(track.artist, 60))}"
    position = player.position if state is not PlayerState.LOADING else 0.0
    embed.description += "\n\n" + ("🔴 LIVE" if is_live else progress_bar(position, duration))
    requester = f"<@{track.requester_id}>" if track.requester_id else "—"
    embed.add_field(name=t(locale, "panel.requested_by"), value=requester, inline=True)
    embed.add_field(name=t(locale, "panel.volume"), value=f"{round(player.volume * 100)}%", inline=True)
    embed.add_field(
        name=t(locale, "panel.loop"),
        value=f"{loop_icon(player.queue.loop)} {loop_label(locale, player.queue.loop)}",
        inline=True,
    )
    upcoming = player.queue.upcoming
    if upcoming:
        lines = [f"`{i}.` {track_link(tr, 50)}" for i, tr in enumerate(upcoming[:3], start=1)]
        if len(upcoming) > 3:
            lines.append(t(locale, "panel.more_in_queue", count=len(upcoming) - 3))
        embed.add_field(name=t(locale, "panel.up_next"), value="\n".join(lines), inline=False)
    if show_artwork and track.artwork_url:
        embed.set_thumbnail(url=track.artwork_url)
    footer = t(locale, "panel.footer", count=len(upcoming), duration=format_duration(player.queue.total_duration))
    if premium:
        footer = f"{footer} • {ICON['premium']} Premium"
    embed.set_footer(text=footer)
    return embed


PanelHandler = Callable[[discord.Interaction, str], Awaitable[None]]


class PanelView(discord.ui.View):
    """Persistent player controls. One handler serves every guild's panel."""

    def __init__(self, handler: PanelHandler, *, locale: str = "en", player: GuildPlayer | None = None) -> None:
        super().__init__(timeout=None)
        self._handler = handler
        from .player import PlayerState

        paused = player is not None and player.state is PlayerState.PAUSED
        active = player is not None and player.current is not None and player.state is not PlayerState.ENDED
        loop = player.queue.loop if player is not None else LoopMode.OFF
        has_history = player is not None and bool(player.queue.history)
        buttons = [
            ("previous", ICON["previous"], t(locale, "btn.previous"), discord.ButtonStyle.secondary, 0, not has_history),
            (
                "toggle",
                ICON["play"] if paused else ICON["pause"],
                t(locale, "btn.resume") if paused else t(locale, "btn.pause"),
                discord.ButtonStyle.success if paused else discord.ButtonStyle.primary,
                0,
                not active,
            ),
            ("skip", ICON["skip"], t(locale, "btn.skip"), discord.ButtonStyle.primary, 0, not active),
            ("stop", ICON["stop"], t(locale, "btn.stop"), discord.ButtonStyle.danger, 0, player is None or player.state is PlayerState.ENDED),
            ("loop", loop_icon(loop), loop_label(locale, loop), discord.ButtonStyle.secondary, 1, False),
            ("shuffle", ICON["shuffle"], t(locale, "btn.shuffle"), discord.ButtonStyle.secondary, 1, player is None or len(player.queue.upcoming) < 2),
            ("vol_down", ICON["vol_down"], t(locale, "btn.vol_down"), discord.ButtonStyle.secondary, 1, False),
            ("vol_up", ICON["vol_up"], t(locale, "btn.vol_up"), discord.ButtonStyle.secondary, 1, False),
            ("queue", ICON["queue"], t(locale, "btn.queue"), discord.ButtonStyle.secondary, 2, False),
            ("favorite", ICON["favorite"], t(locale, "btn.favorite"), discord.ButtonStyle.secondary, 2, not active),
        ]
        ended = player is not None and player.state is PlayerState.ENDED
        for action, emoji, label, style, row, disabled in buttons:
            button: discord.ui.Button[PanelView] = discord.ui.Button(
                style=style, emoji=emoji, label=label, custom_id=PANEL_PREFIX + action,
                row=row, disabled=disabled or ended,
            )
            button.callback = self._make_callback(action)  # type: ignore[method-assign,assignment]
            self.add_item(button)

    def _make_callback(self, action: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._handler(interaction, action)

        return callback


# --------------------------------------------------------------------------- queue view


def queue_embed(player: GuildPlayer | None, locale: str, page: int, per_page: int = 10) -> tuple[discord.Embed, int]:
    embed = _base(f"{ICON['queue']} {t(locale, 'queue.title')}", None, COLOR_PRIMARY)
    if player is None or player.queue.is_empty:
        embed.description = t(locale, "queue.empty_hint")
        return embed, 1
    current = player.current
    if current is not None:
        embed.add_field(
            name=t(locale, "panel.now"),
            value=f"{track_link(current, 70)} `{format_duration(current.duration)}`",
            inline=False,
        )
    upcoming = player.queue.upcoming
    pages = max(1, math.ceil(len(upcoming) / per_page))
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    lines = [
        f"`{i}.` {track_link(tr, 55)} `{format_duration(tr.duration)}`"
        for i, tr in enumerate(upcoming[start : start + per_page], start=start + 1)
    ]
    embed.add_field(
        name=t(locale, "panel.up_next"),
        value="\n".join(lines) if lines else t(locale, "queue.nothing_next"),
        inline=False,
    )
    embed.set_footer(
        text=t(
            locale, "queue.footer", page=page, pages=pages, count=len(upcoming),
            duration=format_duration(player.queue.total_duration),
            loop=loop_label(locale, player.queue.loop),
        )
    )
    return embed, pages


class QueueView(discord.ui.View):
    def __init__(
        self,
        *,
        locale: str,
        get_player: Callable[[], GuildPlayer | None],
        on_jump: Callable[[discord.Interaction, int], Awaitable[None]],
        owner_id: int,
        page: int = 1,
    ) -> None:
        super().__init__(timeout=180)
        self.locale, self.get_player, self.on_jump, self.owner_id = locale, get_player, on_jump, owner_id
        self.page = page
        self.message: discord.Message | None = None
        self._rebuild()

    def render(self) -> discord.Embed:
        embed, pages = queue_embed(self.get_player(), self.locale, self.page)
        self.page = min(self.page, pages)
        self.prev_button.disabled = self.page <= 1
        self.next_button.disabled = self.page >= pages
        return embed

    def _rebuild(self) -> None:
        self.clear_items()
        self.prev_button: discord.ui.Button[QueueView] = discord.ui.Button(
            emoji="◀️", label=t(self.locale, "btn.prev_page"), style=discord.ButtonStyle.secondary, row=0
        )
        self.next_button: discord.ui.Button[QueueView] = discord.ui.Button(
            emoji="▶️", label=t(self.locale, "btn.next_page"), style=discord.ButtonStyle.secondary, row=0
        )
        refresh: discord.ui.Button[QueueView] = discord.ui.Button(
            emoji="🔄", label=t(self.locale, "btn.refresh"), style=discord.ButtonStyle.secondary, row=0
        )
        self.prev_button.callback = self._prev  # type: ignore[method-assign]
        self.next_button.callback = self._next  # type: ignore[method-assign]
        refresh.callback = self._on_refresh  # type: ignore[method-assign]
        for item in (self.prev_button, self.next_button, refresh):
            self.add_item(item)
        player = self.get_player()
        if player is not None and player.queue.upcoming:
            start = (self.page - 1) * 10
            chunk = player.queue.upcoming[start : start + 10]
            if chunk:
                select: discord.ui.Select[QueueView] = discord.ui.Select(
                    placeholder=t(self.locale, "queue.jump_placeholder"),
                    options=[
                        discord.SelectOption(label=truncate(f"{i}. {tr.title}", 100), value=str(i))
                        for i, tr in enumerate(chunk, start=start + 1)
                    ],
                    row=1,
                )

                async def on_select(interaction: discord.Interaction) -> None:
                    await self.on_jump(interaction, int(select.values[0]))

                select.callback = on_select  # type: ignore[method-assign]
                self.add_item(select)

    async def _update(self, interaction: discord.Interaction) -> None:
        self._rebuild()
        embed = self.render()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _prev(self, interaction: discord.Interaction) -> None:
        self.page -= 1
        await self._update(interaction)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page += 1
        await self._update(interaction)

    async def _on_refresh(self, interaction: discord.Interaction) -> None:
        await self._update(interaction)

    async def on_timeout(self) -> None:
        if self.message is not None:
            for item in self.children:
                if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                    item.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# --------------------------------------------------------------------------- generic views


class ConfirmView(discord.ui.View):
    def __init__(self, *, locale: str, owner_id: int, danger_label: str, timeout: float = 60) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.confirmed: bool | None = None
        confirm: discord.ui.Button[ConfirmView] = discord.ui.Button(label=danger_label, style=discord.ButtonStyle.danger)
        cancel: discord.ui.Button[ConfirmView] = discord.ui.Button(label=t(locale, "btn.cancel"), style=discord.ButtonStyle.secondary)
        confirm.callback = self._confirm  # type: ignore[method-assign]
        cancel.callback = self._cancel  # type: ignore[method-assign]
        self.add_item(confirm)
        self.add_item(cancel)
        self.interaction: discord.Interaction | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    async def _confirm(self, interaction: discord.Interaction) -> None:
        self.confirmed, self.interaction = True, interaction
        self.stop()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.confirmed, self.interaction = False, interaction
        self.stop()


class TrackPickerView(discord.ui.View):
    """A select menu of tracks (history, favorites, playlists) to queue."""

    def __init__(
        self,
        *,
        locale: str,
        tracks: list[Track],
        on_pick: Callable[[discord.Interaction, list[Track]], Awaitable[None]],
        owner_id: int,
        placeholder_key: str = "picker.placeholder",
        allow_all: bool = True,
    ) -> None:
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.tracks = tracks[:25]
        options = [
            discord.SelectOption(
                label=truncate(tr.title, 100), value=str(i), description=format_duration(tr.duration)
            )
            for i, tr in enumerate(self.tracks)
        ]
        select: discord.ui.Select[TrackPickerView] = discord.ui.Select(
            placeholder=t(locale, placeholder_key), options=options, min_values=1, max_values=len(options)
        )

        async def picked(interaction: discord.Interaction) -> None:
            chosen = [self.tracks[int(v)] for v in select.values]
            await on_pick(interaction, chosen)

        select.callback = picked  # type: ignore[method-assign]
        self.add_item(select)
        if allow_all and len(self.tracks) > 1:
            all_button: discord.ui.Button[TrackPickerView] = discord.ui.Button(
                label=t(locale, "picker.queue_all", count=len(self.tracks)), style=discord.ButtonStyle.primary,
                emoji=ICON["play"],
            )

            async def queue_all(interaction: discord.Interaction) -> None:
                await on_pick(interaction, list(self.tracks))

            all_button.callback = queue_all  # type: ignore[method-assign]
            self.add_item(all_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(t("en", "picker.not_yours"), ephemeral=True)
            return False
        return True


def link_button(label: str, url: str, emoji: str | None = None) -> discord.ui.Button[Any]:
    return discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=url, emoji=emoji)
