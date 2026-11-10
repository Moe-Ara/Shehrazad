"""/anime picture and /anime react: cute, safe-for-work anime images and reaction GIFs."""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import ui
from ..anime import PICTURES, REACTIONS, AnimeResult, AnimeUnavailable
from ..errors import UserError
from ..interactions import run
from ..service import Actor, Reply

if TYPE_CHECKING:
    from ..bot import ShehrazadBot

log = logging.getLogger(__name__)


def anime_embed(result: AnimeResult, description: str | None) -> discord.Embed:
    embed = ui.info(None, description)
    embed.set_image(url=result.url)
    credit = f"Art: {result.artist_name}" if result.artist_name else (f"From {result.anime_name}" if result.anime_name else None)
    embed.set_footer(text=" · ".join(part for part in (credit, "via nekos.best") if part))
    return embed


def picture_description(result: AnimeResult, label: str) -> str:
    source = f" · [Source]({result.source_url})" if result.source_url else ""
    return f"**{label}**{source}"


def reaction_text(action: str, member: discord.abc.User, target: discord.abc.User | None) -> str:
    _label, with_someone, alone = REACTIONS[action]
    if target is not None and target.id != member.id:
        return with_someone.format(a=member.mention, b=target.mention)
    return alone.format(a=member.mention)


class Fun(commands.GroupCog, group_name="anime", group_description="Cute anime pictures and reaction GIFs (always safe for work)"):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot

    async def _get(self, category: str) -> AnimeResult:
        if not self.bot.flags.enabled("feature.anime"):
            raise UserError("feature.disabled", kind="warning")
        try:
            return await self.bot.anime.get(category)
        except AnimeUnavailable as exc:
            log.warning("anime image service unavailable: %s", exc)
            raise UserError("anime.unavailable", kind="warning") from None

    @app_commands.command(name="picture", description="A random cute anime picture (safe for work).")
    @app_commands.describe(kind="What kind of picture (random if you leave it empty)")
    @app_commands.choices(kind=[app_commands.Choice(name=label, value=key) for key, label in PICTURES.items()])
    async def picture(self, interaction: discord.Interaction, kind: app_commands.Choice[str] | None = None) -> None:
        async def handler(actor: Actor) -> Reply:
            category = kind.value if kind else random.choice(list(PICTURES))
            result = await self._get(category)
            return Reply(embed=anime_embed(result, picture_description(result, PICTURES[category])))

        await run(self.bot, interaction, "anime_picture", handler, defer=True)

    @app_commands.command(name="react", description="Send a cute anime reaction GIF, optionally to someone.")
    @app_commands.describe(action="The reaction to send", user="Who it's for (optional)")
    @app_commands.choices(action=[app_commands.Choice(name=label, value=key) for key, (label, _w, _a) in REACTIONS.items()])
    async def react(self, interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member | None = None) -> None:
        async def handler(actor: Actor) -> Reply:
            result = await self._get(action.value)
            return Reply(embed=anime_embed(result, reaction_text(action.value, actor.member, user)))

        await run(self.bot, interaction, "anime_react", handler, defer=True)


async def setup(bot: ShehrazadBot) -> None:
    await bot.add_cog(Fun(bot))
