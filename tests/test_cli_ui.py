from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from musicbot import cli, ui
from musicbot.player import GuildPlayer, PlayerState
from musicbot.queue import LoopMode
from tests.conftest import FakeHooks, FakeRegistry, FakeSource, FakeVoice, make_track, settle


async def test_cli_grant_status_revoke_and_audit(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    db_path = str(tmp_path / "cli.db")
    assert await cli.run(["--db", db_path, "--as", "moe", "premium", "grant", "123", "--reason", "owner"]) == 0
    assert "granted premium to guild 123" in capsys.readouterr().out
    await cli.run(["--db", db_path, "premium", "status", "123"])
    assert '"tier": "premium"' in capsys.readouterr().out
    await cli.run(["--db", db_path, "--as", "moe", "premium", "revoke", "123", "--reason", "done"])
    await cli.run(["--db", db_path, "premium", "status", "123"])
    assert '"tier": "free"' in capsys.readouterr().out
    await cli.run(["--db", db_path, "audit"])
    out = capsys.readouterr().out
    assert "premium.grant" in out and "premium.revoke" in out and "operator:moe" in out


async def test_cli_flags(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    db_path = str(tmp_path / "cli.db")
    await cli.run(["--db", db_path, "flag", "set", "provider.youtube", "off"])
    await cli.run(["--db", db_path, "flag", "list"])
    assert '"enabled": false' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        await cli.run(["--db", db_path, "flag", "set", "not.a.flag", "off"])


async def _player(**kw):
    p = GuildPlayer(1, registry=FakeRegistry(), hooks=FakeHooks(), idle_timeout=lambda: None,  # type: ignore[arg-type]
                    max_queue=lambda: 200, source_factory=FakeSource)
    p.attach(FakeVoice())
    return p


async def test_now_playing_embed_and_panel_states():
    p = await _player()
    idle = ui.now_playing_embed(p, "en", show_artwork=False, premium=False)
    assert "Nothing playing" in idle.title
    await p.enqueue([make_track(1, requester=42), make_track(2), make_track(3), make_track(4), make_track(5)])
    await settle()
    embed = ui.now_playing_embed(p, "en", show_artwork=False, premium=True)
    assert "Track 1" in embed.description and "<@42>" in embed.fields[0].value
    assert "Premium" in embed.footer.text and "…and 1 more" in embed.fields[3].value
    view = ui.PanelView(AsyncMock(), locale="en", player=p)
    ids = {item.custom_id for item in view.children}
    assert ids == {ui.PANEL_PREFIX + a for a in ("previous", "toggle", "skip", "stop", "loop", "shuffle", "vol_down", "vol_up", "queue", "favorite")}
    by_id = {item.custom_id: item for item in view.children}
    assert by_id[ui.PANEL_PREFIX + "previous"].disabled  # no history yet
    assert all(item.label for item in view.children)  # accessible text labels
    await p.pause()
    paused = {i.custom_id: i for i in ui.PanelView(AsyncMock(), locale="ar", player=p).children}
    assert paused[ui.PANEL_PREFIX + "toggle"].label == "استمرار"
    await p.shutdown("test")
    ended = ui.PanelView(AsyncMock(), locale="en", player=p)
    assert all(item.disabled for item in ended.children)


async def test_queue_embed_pagination_and_loop_label():
    p = await _player()
    await p.enqueue([make_track(i) for i in range(25)])
    await settle()
    await p.set_loop(LoopMode.QUEUE)
    embed, pages = ui.queue_embed(p, "en", page=3)
    assert pages == 3 and "`21.`" in embed.fields[1].value and "Repeat: Queue" in embed.footer.text
    embed, _ = ui.queue_embed(None, "en", page=1)
    assert "empty" in embed.description
    await p.shutdown("test")


def test_progress_bar():
    assert ui.progress_bar(30, 60).startswith("▰▰▰▰▰▰▰▱")
    assert "0:30 / 1:00" in ui.progress_bar(30, 60)
    assert ui.progress_bar(30, None) == "`0:30`"


async def test_stale_panel_click_is_rejected_and_disabled():
    from musicbot.cogs.music import Music
    from musicbot.ops import Metrics, RateLimiter

    p = await _player()
    p.panel_message_id = 1000
    bot = SimpleNamespace(
        service=MagicMock(), limiter=RateLimiter(10, 1), metrics=Metrics(),
        manager=SimpleNamespace(get=lambda gid: p),
    )
    cog = Music.__new__(Music)
    cog.bot, cog.svc = bot, bot.service
    member = MagicMock(spec=discord.Member)
    member.id = 5
    interaction = MagicMock()
    interaction.guild = SimpleNamespace(id=1)
    interaction.guild_id = 1
    interaction.user = member
    interaction.locale = "en-US"
    interaction.message = SimpleNamespace(id=999, edit=AsyncMock())  # an old panel
    interaction.response.send_message = AsyncMock()
    await cog.handle_panel(interaction, "skip")
    sent = interaction.response.send_message.call_args
    assert sent.kwargs["ephemeral"] is True and "no longer active" in sent.kwargs["embed"].description
    interaction.message.edit.assert_awaited_once_with(view=None)
    bot.service.skip.assert_not_called()
    await p.shutdown("test")


def test_state_enum_values_are_stable():
    assert {s.value for s in PlayerState} == {"idle", "loading", "playing", "paused", "ended"}
