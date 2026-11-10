"""Saved server playlists (Premium), personal favorites and server history."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..interactions import run

if TYPE_CHECKING:
    from ..bot import ShehrazadBot


class Library(commands.GroupCog, group_name="library", group_description="Saved playlists and favorites"):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot
        self.svc = bot.service
        super().__init__()

    async def playlist_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        names = await self.svc.library_names(interaction.guild_id, current)
        return [app_commands.Choice(name=n, value=n) for n in names]

    @app_commands.command(name="save", description="Save the current queue as a server playlist (Premium).")
    @app_commands.describe(name="Playlist name")
    @app_commands.guild_only()
    async def save(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 50]) -> None:
        await run(self.bot, interaction, "library.save", lambda a: self.svc.library_save(a, name))

    @app_commands.command(name="load", description="Queue a saved server playlist (Premium).")
    @app_commands.describe(name="Playlist name", shuffle="Shuffle before queueing")
    @app_commands.autocomplete(name=playlist_autocomplete)
    @app_commands.guild_only()
    async def load(self, interaction: discord.Interaction, name: str, shuffle: bool = False) -> None:
        await run(self.bot, interaction, "library.load", lambda a: self.svc.library_load(a, name, shuffle=shuffle), defer=True)

    @app_commands.command(name="list", description="List this server's saved playlists.")
    @app_commands.guild_only()
    async def list_(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "library.list", self.svc.library_list)

    @app_commands.command(name="view", description="Show the tracks in a saved playlist.")
    @app_commands.autocomplete(name=playlist_autocomplete)
    @app_commands.guild_only()
    @app_commands.describe(name="Playlist name")
    async def view(self, interaction: discord.Interaction, name: str) -> None:
        await run(self.bot, interaction, "library.view", lambda a: self.svc.library_view(a, name))

    @app_commands.command(name="delete", description="Delete a saved playlist.")
    @app_commands.autocomplete(name=playlist_autocomplete)
    @app_commands.guild_only()
    @app_commands.describe(name="Playlist name")
    async def delete(self, interaction: discord.Interaction, name: str) -> None:
        await run(self.bot, interaction, "library.delete", lambda a: self.svc.library_delete(a, name))

    @app_commands.command(name="favorites", description="Your favorite tracks — pick some to queue.")
    @app_commands.guild_only()
    async def favorites(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "library.favorites", self.svc.favorites)

    @app_commands.command(name="like", description="Add the current track to your favorites.")
    @app_commands.guild_only()
    async def like(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "library.like", self.svc.favorite_current)


class History(commands.Cog):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot

    @app_commands.command(name="history", description="Recently played in this server — pick some to queue again.")
    @app_commands.guild_only()
    async def history(self, interaction: discord.Interaction) -> None:
        await run(self.bot, interaction, "history", self.bot.service.history)


async def setup(bot: ShehrazadBot) -> None:
    await bot.add_cog(Library(bot))
    await bot.add_cog(History(bot))
