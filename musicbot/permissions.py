"""Authorization rules. Pure functions over member/settings so they are testable.

Rules:
  * Server managers (Manage Server / Administrator) are always DJs.
  * Members with the configured DJ role are DJs.
  * Without a DJ role and with DJ mode off, everyone may control playback.
  * DJ mode restricts playback controls to DJs; `request_mode = dj`
    restricts adding songs to DJs too.
  * Controls require being in the bot's voice channel (DJs included), so
    nobody can grief a session they can't hear.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import discord

from .store import GuildSettings

# Minimum permissions the bot needs. Used for the invite URL and diagnostics.
TEXT_PERMISSIONS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
}
VOICE_PERMISSIONS = {
    "view_channel": "View Channel",
    "connect": "Connect",
    "speak": "Speak",
}


def invite_permissions() -> discord.Permissions:
    perms = discord.Permissions.none()
    for name in {*TEXT_PERMISSIONS, *VOICE_PERMISSIONS}:
        setattr(perms, name, True)
    return perms


def invite_url(application_id: int) -> str:
    return discord.utils.oauth_url(
        application_id,
        permissions=invite_permissions(),
        scopes=("bot", "applications.commands"),
    )


def missing(perms: discord.Permissions, required: dict[str, str]) -> list[str]:
    return [label for name, label in required.items() if not getattr(perms, name, False)]


class MemberLike(Protocol):
    id: int

    @property
    def guild_permissions(self) -> discord.Permissions: ...

    @property
    def roles(self) -> Sequence[discord.abc.Snowflake]: ...


def is_manager(member: MemberLike) -> bool:
    perms = member.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


def is_dj(member: MemberLike, settings: GuildSettings) -> bool:
    if is_manager(member):
        return True
    if settings.dj_role_id is not None:
        return any(role.id == settings.dj_role_id for role in member.roles)
    return not settings.dj_mode


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None  # i18n key

    def __bool__(self) -> bool:
        return self.allowed


ALLOW = Decision(True)


def can_request(member: MemberLike, settings: GuildSettings) -> Decision:
    if settings.request_mode == "dj" and not is_dj(member, settings):
        return Decision(False, "perm.requests_dj_only")
    return ALLOW


def can_control(member: MemberLike, settings: GuildSettings, *, owns_current: bool = False) -> Decision:
    """Pause/seek/volume/loop/queue edits. Requesters may control their own track."""
    if settings.dj_mode and not is_dj(member, settings) and not owns_current:
        return Decision(False, "perm.dj_only")
    return ALLOW


def can_manage_settings(member: MemberLike) -> Decision:
    return ALLOW if is_manager(member) else Decision(False, "perm.manage_guild")


def voice_check(
    member_channel_id: int | None, bot_channel_id: int | None, *, bot_connected: bool
) -> Decision:
    if member_channel_id is None:
        return Decision(False, "voice.join_first")
    if bot_connected and bot_channel_id is not None and member_channel_id != bot_channel_id:
        return Decision(False, "voice.same_channel")
    return ALLOW


def votes_needed(listeners: int, ratio: float) -> int:
    """Votes needed to skip, counting human listeners (excluding bots)."""
    if listeners <= 1:
        return 1
    needed = int(listeners * ratio + 0.999999)
    return max(1, min(listeners, needed))
