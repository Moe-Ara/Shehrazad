"""Playback and queue slash commands, plus the Now Playing panel buttons."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import ui
from ..errors import UserError
from ..i18n import resolve_locale, t
from ..interactions import error_reply, run, send
from ..logging_setup import bind
from ..queue import LoopMode
from ..service import Actor, Reply
from ..util import parse_timestamp

if TYPE_CHECKING:
    from ..bot import ShehrazadBot


class Music(commands.Cog):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot
        self.svc = bot.service

    # ------------------------------------------------------------------ autocomplete

    async def track_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        try:
            options = await self.svc.autocomplete_tracks(interaction.guild_id, interaction.user.id, current)
        except Exception:
            return []
        choices = [app_commands.Choice(name=name, value=value) for name, value in options]
        if current.strip() and len(current) <= 100:
            choices.insert(0, app_commands.Choice(name=f"🔎 {current[:96]}", value=current[:100]))
        return choices[:25]

    # ------------------------------------------------------------------ playback

    @app_commands.command(name="play", description="Play a song or playlist by name or link.")
    @app_commands.describe(
        query="Song name, YouTube/SoundCloud link or playlist link",
        file="Or upload an audio file to play",
        next="Put it at the front of the queue",
        shuffle="Shuffle the tracks of a playlist before queueing",
    )
    @app_commands.autocomplete(query=track_autocomplete)
    @app_commands.guild_only()
    async def play(
        self,
        interaction: discord.Interaction,
        query: str | None = None,
        file: discord.Attachment | None = None,
        next: bool = False,
        shuffle: bool = False,
    ) -> None:
        async def handler(actor: Actor) -> Reply:
            target = file.url if file is not None else (query or "").strip()
            if not target:
                raise UserError("play.need_query", kind="info")
            return await self.svc.play(actor, target, next_up=next, shuffle=shuffle)

        await run(self.bot, interaction, "play", handler, defer=True)

    @app_commands.command(name="playlist", description="Queue every track from a playlist or album link.")
    @app_commands.describe(url="Playlist or album link", shuffle="Shuffle the tracks before queueing")
    @app_commands.guild_only()
    async def playlist(self, interaction: discord.Interaction, url: str, shuffle: bool = False) -> None:
        await run(self.bot, interaction, "playlist", lambda a: self.svc.play(a, url, shuffle=shuffle), defer=True)

    @app_commands.command(name="pause", description="Pause playback.")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "pause", self.svc.pause)

    @app_commands.command(name="resume", description="Resume playback.")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "resume", self.svc.resume)

    @app_commands.command(name="skip", description="Skip the current track (starts a vote if needed).")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "skip", self.svc.skip)

    @app_commands.command(name="previous", description="Go back to the previous track.")
    @app_commands.guild_only()
    async def previous(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "previous", self.svc.previous)

    @app_commands.command(name="stop", description="Stop playback, clear the queue and leave the voice channel.")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "stop", self.svc.stop)

    @app_commands.command(name="seek", description="Jump to a time in the current track, e.g. 1:30.")
    @app_commands.describe(time="Position like 1:30, 90 or 1m30s")
    @app_commands.guild_only()
    async def seek(self, interaction: discord.Interaction, time: str) -> None:
        async def handler(actor: Actor) -> Reply:
            seconds = parse_timestamp(time)
            if seconds is None:
                raise UserError("player.bad_time", kind="warning")
            return await self.svc.seek(actor, seconds)

        await run(self.bot, interaction, "seek", handler)

    @app_commands.command(name="nowplaying", description="Show what's playing right now.")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "nowplaying", self.svc.nowplaying)

    @app_commands.command(name="volume", description="Show or set the volume (1-150%).")
    @app_commands.describe(level="Volume in percent")
    @app_commands.guild_only()
    async def volume(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 150] | None = None) -> None:
        await run(self.bot, interaction, "volume", lambda a: self.svc.volume(a, level))

    @app_commands.command(name="loop", description="Repeat the track, the whole queue, or turn repeat off.")
    @app_commands.describe(mode="Leave empty to cycle through the modes")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Off", value="off"),
            app_commands.Choice(name="Current track", value="track"),
            app_commands.Choice(name="Whole queue", value="queue"),
        ]
    )
    @app_commands.guild_only()
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str] | None = None) -> None:
        selected = LoopMode(mode.value) if mode else None
        await run(self.bot, interaction, "loop", lambda a: self.svc.loop(a, selected))

    @app_commands.command(name="join", description="Join your voice channel.")
    @app_commands.guild_only()
    async def join(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "join", self.svc.join, defer=True)

    @app_commands.command(name="leave", description="Leave the voice channel and end the session.")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "leave", self.svc.leave)

    @app_commands.command(name="restore", description="Restore the last saved queue (Premium).")
    @app_commands.guild_only()
    async def restore(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "restore", self.svc.restore, defer=True)

    # ------------------------------------------------------------------ queue

    @app_commands.command(name="queue", description="Show the queue.")
    @app_commands.describe(page="Page number")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 500] = 1) -> None:
        async def handler(actor: Actor) -> Reply:
            return self.svc.queue_reply(actor, page)

        await run(self.bot, interaction, "queue", handler)

    @app_commands.command(name="remove", description="Remove a track from the queue.")
    @app_commands.describe(position="Position in the queue (see /queue)")
    @app_commands.guild_only()
    async def remove(self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 5000]) -> None:
        await run(self.bot, interaction, "remove", lambda a: self.svc.remove(a, position))

    @app_commands.command(name="move", description="Move a track to another position in the queue.")
    @app_commands.describe(source="Current position", target="New position")
    @app_commands.guild_only()
    async def move(
        self,
        interaction: discord.Interaction,
        source: app_commands.Range[int, 1, 5000],
        target: app_commands.Range[int, 1, 5000],
    ) -> None:
        await run(self.bot, interaction, "move", lambda a: self.svc.move(a, source, target))

    @app_commands.command(name="clear", description="Remove every upcoming track (keeps the current one).")
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "clear", self.svc.clear)

    @app_commands.command(name="shuffle", description="Shuffle the upcoming tracks.")
    @app_commands.guild_only()
    async def shuffle(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "shuffle", self.svc.shuffle)

    @app_commands.command(name="jump", description="Skip straight to a track in the queue.")
    @app_commands.describe(position="Position in the queue (see /queue)")
    @app_commands.guild_only()
    async def jump(self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 5000]) -> None:
        await run(self.bot, interaction, "jump", lambda a: self.svc.jump(a, position))

    # ------------------------------------------------------------------ panel buttons

    async def handle_panel(self, interaction: discord.Interaction, action: str) -> None:
        """Dispatch a Now Playing panel button. Stale panels are rejected and disabled."""
        with bind(guild_id=interaction.guild_id) as cid:
            locale = resolve_locale("auto", str(interaction.locale))
            if interaction.guild is None or not isinstance(interaction.user, discord.Member):
                return
            if not self.bot.limiter.allow(interaction.user.id):
                self.bot.metrics.rate_limited.inc()
                await interaction.response.send_message(embed=ui.warning(t(locale, "error.rate_limited")), ephemeral=True)
                return
            player = self.bot.manager.get(interaction.guild.id)
            message_id = interaction.message.id if interaction.message else None
            if player is None or player.panel_message_id != message_id:
                await interaction.response.send_message(embed=ui.info(None, t(locale, "panel.stale")), ephemeral=True)
                if interaction.message is not None:
                    with contextlib.suppress(discord.HTTPException):
                        await interaction.message.edit(view=None)
                return
            actor = await self.svc.actor(interaction.guild, interaction.user, interaction.channel_id or 0, str(interaction.locale))
            handlers = {
                "toggle": self.svc.toggle_pause,
                "skip": self.svc.skip,
                "previous": self.svc.previous,
                "stop": self.svc.stop,
                "loop": lambda a: self.svc.loop(a, None),
                "shuffle": self.svc.shuffle,
                "vol_down": lambda a: self.svc.nudge_volume(a, -10),
                "vol_up": lambda a: self.svc.nudge_volume(a, 10),
                "favorite": self.svc.favorite_current,
            }
            outcome = "ok"
            try:
                if action == "queue":
                    reply = self.svc.queue_reply(actor)
                elif action in handlers:
                    reply = await handlers[action](actor)
                else:
                    return
                reply.ephemeral = True
            except UserError as exc:
                outcome = "refused"
                reply = error_reply(self.bot, actor.locale, exc, interaction.guild.id)
            except Exception:
                outcome = "error"
                import logging

                logging.getLogger(__name__).exception("panel action %s failed", action)
                reply = Reply(embed=ui.error(t(actor.locale, "error.internal", ref=cid)), ephemeral=True)
            self.bot.metrics.commands.labels(f"panel:{action}", outcome).inc()
            await send(interaction, reply)


async def setup(bot: ShehrazadBot) -> None:
    await bot.add_cog(Music(bot))
