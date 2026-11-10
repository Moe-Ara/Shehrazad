"""Owner-only novelty commands: /heybabe, /whereareyou, /comehere, /goaway.

They are registered only in the servers listed in NOVELTY_GUILD_IDS (never globally,
so they don't appear on top.gg or in anyone else's server), and they answer only the
bot owner (OWNER_IDS, or the Discord application's owner when that is empty).
Everyone else gets a short private "just for my owner" reply.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import ui
from ..errors import UserError
from ..interactions import run
from ..service import Actor, Reply

if TYPE_CHECKING:
    from ..bot import ShehrazadBot

HEY_BABE = [
    "Hey you 💜 What are we listening to tonight?",
    "Hi babe! Pick a song and I'll take care of the rest 🎶",
    "There you are! I missed you. Shall I put something on?",
]
COMING = ["Coming, coming! 💃", "On my way!", "Right behind you 🎶"]
LEAVING = ["I'll do like a banana and split 🍌", "Shehrazad out ✌️", "Bye bye! 👋"]


async def is_owner(bot: ShehrazadBot, user: discord.abc.User) -> bool:
    if bot.config.owner_ids:
        return user.id in bot.config.owner_ids
    return await bot.is_owner(user)


async def require_owner(bot: ShehrazadBot, actor: Actor) -> None:
    if not await is_owner(bot, actor.member):
        raise UserError("novelty.owner_only", kind="info")


def where_text(bot: ShehrazadBot, guild: discord.Guild) -> str:
    voice = guild.voice_client
    channel = getattr(voice, "channel", None)
    player = bot.manager.get(guild.id)
    elsewhere = bot.manager.active_count - (1 if player is not None else 0)
    if channel is None:
        text = "I'm not in a voice channel here. Use /comehere and I'll join you."
    elif player is not None and player.current is not None:
        text = f"I'm in {channel.mention}, playing **{player.current.title}**."
    else:
        text = f"I'm in {channel.mention}, just hanging out."
    if elsewhere > 0:
        text += f" I'm also playing in {elsewhere} other server{'s' if elsewhere != 1 else ''}."
    return text


class Novelty(commands.Cog):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot

    @app_commands.command(name="heybabe", description="Say hi to Shehrazad (owner only).")
    @app_commands.guild_only()
    async def heybabe(self, interaction: discord.Interaction) -> None:
        async def handler(actor: Actor) -> Reply:
            await require_owner(self.bot, actor)
            return Reply(content=random.choice(HEY_BABE))

        await run(self.bot, interaction, "heybabe", handler)

    @app_commands.command(name="whereareyou", description="Ask Shehrazad where she is (owner only).")
    @app_commands.guild_only()
    async def whereareyou(self, interaction: discord.Interaction) -> None:
        async def handler(actor: Actor) -> Reply:
            await require_owner(self.bot, actor)
            return Reply(content=where_text(self.bot, actor.guild))

        await run(self.bot, interaction, "whereareyou", handler)

    @app_commands.command(name="comehere", description="Call Shehrazad into your voice channel (owner only).")
    @app_commands.guild_only()
    async def comehere(self, interaction: discord.Interaction) -> None:
        async def handler(actor: Actor) -> Reply:
            await require_owner(self.bot, actor)
            reply = await self.bot.service.join(actor)
            reply.embed = ui.success(random.choice(COMING))
            return reply

        await run(self.bot, interaction, "comehere", handler)

    @app_commands.command(name="goaway", description="Send Shehrazad out of the voice channel (owner only).")
    @app_commands.guild_only()
    async def goaway(self, interaction: discord.Interaction) -> None:
        async def handler(actor: Actor) -> Reply:
            await require_owner(self.bot, actor)
            reply = await self.bot.service.leave(actor)
            reply.embed = ui.success(random.choice(LEAVING))
            return reply

        await run(self.bot, interaction, "goaway", handler)


async def setup(bot: ShehrazadBot) -> None:
    if bot.config.novelty_guild_ids:
        guilds = [discord.Object(id=guild_id) for guild_id in sorted(bot.config.novelty_guild_ids)]
        await bot.add_cog(Novelty(bot), guilds=guilds)
