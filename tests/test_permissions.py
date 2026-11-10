from types import SimpleNamespace

import discord

from musicbot import permissions as perms
from musicbot.store import GuildSettings

DJ_ROLE = 777


def member(*, manage=False, roles=()):
    p = discord.Permissions.none()
    p.manage_guild = manage
    return SimpleNamespace(id=1, guild_permissions=p, roles=[SimpleNamespace(id=r) for r in roles])


def test_everyone_is_dj_without_role_or_dj_mode():
    assert perms.is_dj(member(), GuildSettings(guild_id=1))


def test_dj_role_restricts_dj_status():
    settings = GuildSettings(guild_id=1, dj_role_id=DJ_ROLE)
    assert not perms.is_dj(member(), settings)
    assert perms.is_dj(member(roles=[DJ_ROLE]), settings)
    assert perms.is_dj(member(manage=True), settings)


def test_dj_mode_blocks_controls_but_allows_requester_of_current_track():
    settings = GuildSettings(guild_id=1, dj_mode=True)
    decision = perms.can_control(member(), settings)
    assert not decision and decision.reason == "perm.dj_only"
    assert perms.can_control(member(), settings, owns_current=True)
    assert perms.can_control(member(manage=True), settings)


def test_request_mode():
    settings = GuildSettings(guild_id=1, request_mode="dj", dj_role_id=DJ_ROLE)
    assert not perms.can_request(member(), settings)
    assert perms.can_request(member(roles=[DJ_ROLE]), settings)
    assert perms.can_request(member(), GuildSettings(guild_id=1))


def test_settings_require_manage_guild():
    assert not perms.can_manage_settings(member())
    assert perms.can_manage_settings(member(manage=True))


def test_voice_check():
    assert perms.voice_check(None, None, bot_connected=False).reason == "voice.join_first"
    assert perms.voice_check(5, 6, bot_connected=True).reason == "voice.same_channel"
    assert perms.voice_check(5, 6, bot_connected=False)
    assert perms.voice_check(5, 5, bot_connected=True)


def test_votes_needed():
    assert perms.votes_needed(1, 0.5) == 1
    assert perms.votes_needed(2, 0.5) == 1
    assert perms.votes_needed(3, 0.5) == 2
    assert perms.votes_needed(4, 0.5) == 2
    assert perms.votes_needed(5, 1.0) == 5


def test_invite_permissions_are_minimal():
    p = perms.invite_permissions()
    assert p.connect and p.speak and p.send_messages and p.embed_links
    assert not (p.administrator or p.manage_messages or p.manage_guild or p.mention_everyone)
    url = perms.invite_url(123)
    assert "applications.commands" in url and "client_id=123" in url
