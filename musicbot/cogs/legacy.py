"""Legacy prefix commands (`!play`, `!p`, `!l3be`, ...), kept for existing communities.

Loaded only when LEGACY_PREFIX_COMMANDS=true because it requires the
privileged Message Content intent. Every command routes through the same
MusicService as the slash commands, so permissions and limits are identical.
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
from ..service import Actor, Reply

if TYPE_CHECKING:
    from ..bot import ShehrazadBot

log = logging.getLogger(__name__)

COMING_TO_VC = ["لحظة جاية", "جاية يالله", "جايتك"]
LEAVING_VC = ["I'll do like a banana and split", "Shehrazad out", "يالله باي"]


class Legacy(commands.Cog):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot
        self.svc = bot.service

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
            if reply.view is not None and not reply.ephemeral:
                kwargs["view"] = reply.view
            message = await ctx.reply(mention_author=False, delete_after=30 if reply.ephemeral else None, **kwargs)  # type: ignore[call-overload]
            if reply.view is not None and hasattr(reply.view, "message"):
                reply.view.message = message  # type: ignore[attr-defined]

    @commands.command(name="play", aliases=["p", "l3be"])
    async def play(self, ctx: commands.Context[ShehrazadBot], *, query: str = "") -> None:
        target = ctx.message.attachments[0].url if ctx.message.attachments else query
        if not target:
            return
        await self._run(ctx, "play", lambda a: self.svc.play(a, target))

    @commands.command(name="playlist", aliases=["pl"])
    async def playlist(self, ctx: commands.Context[ShehrazadBot], *, url: str) -> None:
        await self._run(ctx, "playlist", lambda a: self.svc.play(a, url))

    @commands.command(name="shuffleplay", aliases=["sp"])
    async def shuffleplay(self, ctx: commands.Context[ShehrazadBot], *, url: str) -> None:
        await self._run(ctx, "shuffleplay", lambda a: self.svc.play(a, url, shuffle=True))

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

    @commands.command(name="skip", aliases=["s"])
    async def skip(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "skip", self.svc.skip)

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying(self, ctx: commands.Context[ShehrazadBot]) -> None:
        async def handler(actor: Actor) -> Reply:
            reply = await self.svc.nowplaying(actor)
            reply.ephemeral = False
            return reply

        await self._run(ctx, "nowplaying", handler)

    @commands.command(name="volume", aliases=["vol", "v"])
    async def volume(self, ctx: commands.Context[ShehrazadBot], percent: int | None = None) -> None:
        await self._run(ctx, "volume", lambda a: self.svc.volume(a, percent))

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx: commands.Context[ShehrazadBot]) -> None:
        async def handler(actor: Actor) -> Reply:
            return self.svc.queue_reply(actor)

        await self._run(ctx, "queue", handler)

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "pause", self.svc.pause)

    @commands.command(name="resume")
    async def resume(self, ctx: commands.Context[ShehrazadBot]) -> None:
        await self._run(ctx, "resume", self.svc.resume)

    @commands.command(name="babe", aliases=["shosho", "bae"])
    async def babe(self, ctx: commands.Context[ShehrazadBot]) -> None:
        """Private easter egg from the original bot; only answers its two original users."""
        replies = {410821336072716288: "شو تشكل اسي", 492867665551949824: "كول خرا حارث"}
        reply = replies.get(ctx.author.id)
        if reply:
            await ctx.send(reply)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context[ShehrazadBot], error: commands.CommandError) -> None:
        if isinstance(error, (commands.CommandNotFound, commands.MissingRequiredArgument, commands.BadArgument)):
            return
        log.warning("legacy command error: %s", type(error).__name__)


async def setup(bot: ShehrazadBot) -> None:
    await bot.add_cog(Legacy(bot))
