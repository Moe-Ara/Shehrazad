"""Boot the real bot object offline (no Discord login) and inspect its wiring."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

from musicbot.bot import ShehrazadBot
from musicbot.config import Settings


@pytest.fixture
async def bot(tmp_path):
    config = Settings(token="x", database_path=tmp_path / "bot.db", health_port=0, sync_commands=False)
    instance = ShehrazadBot(config)
    await instance.setup_hook()
    yield instance
    await instance.close()


async def test_all_cogs_and_commands_load(bot):
    names = {c.qualified_name for c in bot.tree.walk_commands()}
    expected = {
        "play", "playlist", "pause", "resume", "skip", "previous", "stop", "seek", "nowplaying", "volume",
        "loop", "join", "leave", "restore", "queue", "remove", "move", "clear", "shuffle", "jump", "history",
        "library save", "library load", "library list", "library view", "library delete", "library favorites",
        "library like", "settings view", "settings dj-role", "settings stay-connected", "setup", "premium",
        "help", "about", "invite", "support", "status", "privacy", "feedback",
    }
    assert expected <= names
    assert len(bot.tree.get_commands()) <= 100  # Discord's global command limit


async def test_slash_commands_only(bot):
    # No prefix/text commands are registered, and messages never reach the command processor.
    assert list(bot.commands) == []
    assert not bot.intents.message_content
    assert bot.intents.voice_states and bot.intents.guilds and not bot.intents.members
    bot.process_commands = AsyncMock()
    bot.manager.note_message = MagicMock()
    message = MagicMock(content="!play something")
    message.author.bot = False
    await bot.on_message(message)
    bot.process_commands.assert_not_awaited()
    bot.manager.note_message.assert_called_once_with(message)


async def test_command_definitions_are_valid_for_discord(bot):
    payload = [c.to_dict(bot.tree) for c in bot.tree.get_commands()]
    json.dumps(payload)  # serialisable, as sent to Discord
    for command in bot.tree.walk_commands():
        assert 1 <= len(command.description) <= 100, command.qualified_name
        if isinstance(command, app_commands.Command):
            for param in command.parameters:
                assert len(param.description) <= 100


async def test_panel_view_registered_and_services_wired(bot):
    assert bot.manager.panel_handler is not None
    assert bot.persistent_views, "panel buttons must survive restarts"
    assert await bot.db.ping()
    assert bot.premium.tier(1).value == "free"


async def test_health_endpoints(bot):
    live = await bot.health.healthz(None)
    assert live.status == 200 and b"starting" in live.body  # within the startup grace period
    ready = await bot.health.readyz(None)
    assert ready.status == 503  # not connected to Discord in tests
    body = json.loads(ready.body)
    assert body["database"] is True and body["gateway"] is False
    assert "token" not in ready.body.decode().lower()
    metrics = await bot.health.metrics(None)
    assert b"shz_voice_sessions" in metrics.body and b"shz_commands_total" in metrics.body
