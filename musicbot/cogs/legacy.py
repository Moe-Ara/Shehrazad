"""Legacy prefix commands (`!play`, `!p`, `!l3be`, ...), kept for existing communities.

Loaded only when LEGACY_PREFIX_COMMANDS=true because it requires the
privileged Message Content intent. Every command routes through the same
MusicService as the slash commands, so permissions and limits are identical.
Admin flows with pickers (/setup, /settings, /library, /premium, /privacy)
stay slash-only.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from .. import ui
from ..errors import UserError
from ..i18n import t
from ..logging_setup import bind
from ..queue import LoopMode
from ..service import Actor, Reply
from ..util import parse_timestamp

if TYPE_CHECKING:
    from ..bot import ShehrazadBot

log = logging.getLogger(__name__)

COMING_TO_VC = ["لحظة جاية", "جاية يالله", "جايتك"]
LEAVING_VC = ["I'll do like a banana and split", "Shehrazad out", "يالله باي"]

LOOP_WORDS = {
    "off": LoopMode.OFF, "none": LoopMode.OFF, "0": LoopMode.OFF,
    "track": LoopMode.TRACK, "song": LoopMode.TRACK, "one": LoopMode.TRACK, "1": LoopMode.TRACK,
    "queue": LoopMode.QUEUE, "all": LoopMode.QUEUE, "q": LoopMode.QUEUE,
}

# (command name, help text key) in the order !help lists them.
HELP_ORDER = [
    ("play", "cmd.play"),
    ("playnext", "cmd.playnext"),
    ("playlist", "cmd.playlist"),
    ("shuffleplay", "cmd.shuffleplay"),
    ("pause", "cmd.pause"),
    ("resume", "cmd.resume"),
    ("skip", "cmd.skip"),
    ("previous", "cmd.previous"),
    ("seek", "cmd.seek"),
    ("volume", "cmd.volume"),
    ("loop", "cmd.loop"),
    ("nowplaying", "cmd.nowplaying"),
    ("queue", "cmd.queue"),
    ("remove", "cmd.remove"),
    ("move", "cmd.move"),
    ("jump", "cmd.jump"),
    ("shuffle", "cmd.shuffle"),
    ("clear", "cmd.clear"),
    ("like", "cmd.library_like"),
    ("history", "cmd.history"),
    ("restore", "cmd.restore"),
    ("join", "cmd.join"),
    ("leave", "cmd.leave"),
    ("stop", "cmd.stop"),
    ("help", "cmd.help"),
]


class Legacy(commands.Cog):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot
        self.svc = bot.service

    @property
    def prefix(self) -> str:
        return self.bot.config.legacy_prefix

    async def _run(self, ctx: commands.Context[ShehrazadBot], name: str, handler: Callable[[Actor], Awaitable[Reply]]) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if not self.bot.limiter.allow(ctx.author.id):
            return
        with bind(guild_id=ctx.guild.id) as cid:
            actor = await self.svc.actor(ctx.guild, ctx.author, ctx.channel.id, None)
            outcome = "ok"
            try:
                async with ctx.typing():
                    reply = await handler(actor)
            except UserError as exc:
                outcome = "refused"
                reply = Reply(embed=self.svc.error_embed(actor.locale, exc), ephemeral=True)
            except Exception:
                outcome = "error"
                log.exception("legacy command %s failed", name)
                reply = Reply(embed=ui.error(t(actor.locale, "error.internal", ref=cid)), ephemeral=True)
            self.bot.metrics.commands.labels(f"legacy:{name}", outcome).inc()
            kwargs: dict[str, object] = {"embed": reply.embed}
            if reply.content:
                kwargs["content"] = reply.content
            if reply.view is not None and not reply.ephemeral:
                kwargs["view"] = reply.view
            message = await ctx.reply(mention_author=False, delete_after=30 if reply.ephemeral else None, **kwargs)  # type: ignore[call-overload]
            if reply.view is not None and hasattr(reply.view, "message"):
                reply.view.message = message  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ playing

    @commands.command(name="play", aliases=["p", "l3be"], usage="<song name or link>")
    async def play(self, ctx: commands.Context[ShehrazadBot], *, query: str = "") -> None:
        target = ctx.message.attachments[0].url if ctx.message.attachments else query
        if not target:
            await self._usage(ctx)
            return
        await self._run(ctx, "play", lambda a: self.svc.play(a, target))

    @commands.command(name="playnext", aliases=["pn"], usage="<song name or link>")
    async def playnext(self, ctx: commands.Context[ShehrazadBot], *, query: str = "") -> None:
        target = ctx.message.attachments[0].url if ctx.message.attachments else query
        if not target:
            await self._usage(ctx)
            return
        await self._run(ctx, "playnext", lambda a: self.svc.play(a, target, next_up=True))

    @commands.command(name="playlist", aliases=["pl"], usage="<playlist link>")
    async def playlist(self, ctx: commands.Context[ShehrazadBot], *, url: str) -> None:
        await self._run(ctx, "playlist", lambda a: self.svc.play(a, url))

    @commands.command(name="shuffleplay", aliases=["sp"], usage="<playlist link>")
    async def shuffleplay(self, ctx: commands.Context[ShehrazadBot], *, url: str) -> None:
        await self._run(ctx, "shuffleplay", lambda a: self.svc.play(a, url, shuffle=True))

    # ------------------------------------------------------------------ playback controls

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "pause", self.svc.pause)

    @commands.command(name="resume", aliases=["unpause", "continue"])
    async def resume(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "resume", self.svc.resume)

    @commands.command(name="skip", aliases=["s"])
    async def skip(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "skip", self.svc.skip)

    @commands.command(name="previous", aliases=["prev", "back", "b"])
    async def previous(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "previous", self.svc.previous)

    @commands.command(name="seek", usage="<1:30 | 90 | 1m30s>")
    async def seek(self, ctx: commands.Context[ShehrazadBot], *, time: str = "") -> None:
        async def handler(actor: Actor) -> Reply:
            seconds = parse_timestamp(time)
            if seconds is None:
                raise UserError("player.bad_time", kind="warning")
            return await self.svc.seek(actor, seconds)

        await self._run(ctx, "seek", handler)

    @commands.command(name="volume", aliases=["vol", "v"], usage="[1-150]")
    async def volume(self, ctx: commands.Context[ShehrazadBot], percent: int | None = None) -> None:
        if percent is not None:
            percent = max(1, min(150, percent))
        await self._run(ctx, "volume", lambda a: self.svc.volume(a, percent))

    @commands.command(name="loop", aliases=["repeat"], usage="[off | track | queue]")
    async def loop(self, ctx: commands.Context[ShehrazadBot], mode: str = "") -> None:
        async def handler(actor: Actor) -> Reply:
            selected = None
            if mode:
                selected = LOOP_WORDS.get(mode.strip().lower())
                if selected is None:
                    raise UserError("legacy.bad_loop", kind="warning")
            return await self.svc.loop(actor, selected)

        await self._run(ctx, "loop", handler)

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying(self, ctx: commands.Context[ShehrazadBot]) -> None:
        async def handler(actor: Actor) -> Reply:
            reply = await self.svc.nowplaying(actor)
            reply.ephemeral = False
            return reply

        await self._run(ctx, "nowplaying", handler)

    # ------------------------------------------------------------------ queue

    @commands.command(name="queue", aliases=["q"], usage="[page]")
    async def queue(self, ctx: commands.Context[ShehrazadBot], page: int = 1) -> None:
        async def handler(actor: Actor) -> Reply:
            return self.svc.queue_reply(actor, max(1, page))

        await self._run(ctx, "queue", handler)

    @commands.command(name="remove", aliases=["rm"], usage="<position>")
    async def remove(self, ctx: commands.Context[ShehrazadBot], position: int) -> None:
        await self._run(ctx, "remove", lambda a: self.svc.remove(a, position))

    @commands.command(name="move", aliases=["mv"], usage="<from> <to>")
    async def move(self, ctx: commands.Context[ShehrazadBot], source: int, target: int) -> None:
        await self._run(ctx, "move", lambda a: self.svc.move(a, source, target))

    @commands.command(name="jump", aliases=["skipto"], usage="<position>")
    async def jump(self, ctx: commands.Context[ShehrazadBot], position: int) -> None:
        await self._run(ctx, "jump", lambda a: self.svc.jump(a, position))

    @commands.command(name="shuffle", aliases=["mix"])
    async def shuffle(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "shuffle", self.svc.shuffle)

    @commands.command(name="clear", aliases=["cl"])
    async def clear(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "clear", self.svc.clear)

    @commands.command(name="like", aliases=["fav"])
    async def like(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "like", self.svc.favorite_current)

    @commands.command(name="history", aliases=["recent"])
    async def history(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "history", self.svc.history)

    @commands.command(name="restore")
    async def restore(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "restore", self.svc.restore)

    # ------------------------------------------------------------------ voice

    @commands.command(name="join", aliases=["j", "connect", "t3e"])
    async def join(self, ctx: commands.Context[ShehrazadBot]) -> None:
        async def handler(actor: Actor) -> Reply:
            reply = await self.svc.join(actor)
            if actor.locale == "ar":
                reply.embed = ui.success(random.choice(COMING_TO_VC))
            return reply

        await self._run(ctx, "join", handler)

    @commands.command(name="leave", aliases=["l", "disconnect", "ro7e"])
    async def leave(self, ctx: commands.Context[ShehrazadBot]) -> None:
        async def handler(actor: Actor) -> Reply:
            reply = await self.svc.leave(actor)
            reply.embed = ui.success(random.choice(LEAVING_VC))
            return reply

        await self._run(ctx, "leave", handler)

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "stop", self.svc.stop)

    # ------------------------------------------------------------------ help

    def help_embed(self, locale: str) -> discord.Embed:
        lines = []
        for name, key in HELP_ORDER:
            command = self.bot.get_command(name)
            if command is None:
                continue
            aliases = "".join(f", `{self.prefix}{alias}`" for alias in command.aliases)
            usage = f" {command.usage}" if command.usage else ""
            lines.append(f"`{self.prefix}{name}{usage}`{aliases} — {t(locale, key)}")
        embed = ui.info(f"{ui.ICON['music']} {t(locale, 'legacy.help_title', prefix=self.prefix)}", "\n".join(lines))
        embed.set_footer(text=t(locale, "legacy.help_footer"))
        return embed

    @commands.command(name="help", aliases=["h", "commands"])
    async def help(self, ctx: commands.Context[ShehrazadBot]) -> None:
        async def handler(actor: Actor) -> Reply:
            return Reply(embed=self.help_embed(actor.locale))

        await self._run(ctx, "help", handler)

    @commands.command(name="babe", aliases=["shosho", "bae"])
    async def babe(self, ctx: commands.Context[ShehrazadBot]) -> None:
        """Private easter egg from the original bot; only answers its two original users."""
        replies = {410821336072716288: "شو تشكل اسي", 492867665551949824: "كول خرا حارث"}
        reply = replies.get(ctx.author.id)
        if reply:
            await ctx.send(reply)

    # ------------------------------------------------------------------ errors

    async def _usage(self, ctx: commands.Context[ShehrazadBot]) -> None:
        command = ctx.command
        if ctx.guild is None or command is None:
            return
        locale = "en"
        if isinstance(ctx.author, discord.Member):
            locale = (await self.svc.actor(ctx.guild, ctx.author, ctx.channel.id, None)).locale
        usage = f"`{self.prefix}{command.name}{' ' + command.usage if command.usage else ''}`"
        await ctx.reply(embed=ui.warning(t(locale, "legacy.usage", usage=usage)), mention_author=False, delete_after=30)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context[ShehrazadBot], error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            await self._usage(ctx)
            return
        log.warning("legacy command error: %s", type(error).__name__)


async def setup(bot: ShehrazadBot) -> None:
    await bot.add_cog(Legacy(bot))
