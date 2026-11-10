"""Owner-only novelty commands: guild-scoped registration and the owner check."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from musicbot.bot import ShehrazadBot
from musicbot.cogs import novelty
from musicbot.config import Settings
from musicbot.errors import UserError

OWNER = 410821336072716288
NAMES = {"heybabe", "whereareyou", "comehere", "goaway"}


@pytest.fixture
async def bot(tmp_path):
    instance = ShehrazadBot(Settings(
        token="x", database_path=tmp_path / "n.db", health_port=0, sync_commands=False,
        owner_ids=frozenset({OWNER}), novelty_guild_ids=frozenset({111, 222}),
    ))
    await instance.setup_hook()
    yield instance
    await instance.close()


async def test_novelty_commands_exist_only_in_the_configured_servers(bot):
    assert not NAMES & {c.name for c in bot.tree.get_commands()}  # never global, so never on top.gg
    for guild_id in (111, 222):
        assert {c.name for c in bot.tree.get_commands(guild=discord.Object(guild_id))} == NAMES
    assert bot.tree.get_commands(guild=discord.Object(333)) == []


async def test_novelty_cog_not_loaded_without_servers(tmp_path):
    plain = ShehrazadBot(Settings(token="x", database_path=tmp_path / "p.db", health_port=0, sync_commands=False))
    await plain.setup_hook()
    try:
        assert plain.get_cog("Novelty") is None
    finally:
        await plain.close()


async def test_only_the_owner_can_use_them(bot):
    await novelty.require_owner(bot, MagicMock(member=MagicMock(id=OWNER)))
    with pytest.raises(UserError) as exc:
        await novelty.require_owner(bot, MagicMock(member=MagicMock(id=492867665551949824)))
    assert exc.value.key == "novelty.owner_only"


async def test_where_are_you_when_not_connected(bot):
    text = novelty.where_text(bot, MagicMock(id=111, voice_client=None))
    assert text.startswith("I'm not in a voice channel here")


async def test_guild_command_sync_is_skipped_when_unchanged(bot):
    bot.tree.sync = AsyncMock(return_value=[])
    await bot.sync_guild_commands()
    assert bot.tree.sync.await_count == 2  # one per novelty server
    await bot.sync_guild_commands()
    assert bot.tree.sync.await_count == 2  # unchanged -> no API calls
