"""Uniform interaction handling: defer, run, translate errors, reply, measure."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord

from . import ui
from .errors import PremiumRequired, UserError
from .i18n import resolve_locale, t
from .logging_setup import bind
from .service import Actor, Reply

if TYPE_CHECKING:
    from .bot import ShehrazadBot

log = logging.getLogger(__name__)

Handler = Callable[[Actor], Awaitable[Reply]]


async def send(interaction: discord.Interaction, reply: Reply) -> discord.Message | None:
    kwargs: dict[str, object] = {"ephemeral": reply.ephemeral}
    if reply.embed is not None:
        kwargs["embed"] = reply.embed
    if reply.content is not None:
        kwargs["content"] = reply.content
    if reply.view is not None:
        kwargs["view"] = reply.view
    if reply.files:
        kwargs["files"] = reply.files
    message: discord.Message | None = None
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(**kwargs)  # type: ignore[call-overload]
            message = await interaction.original_response()
        else:
            if reply.ephemeral:
                # The public "thinking…" placeholder must not linger above a private reply.
                with contextlib.suppress(discord.HTTPException):
                    original = await interaction.original_response()
                    if not original.flags.ephemeral:
                        await interaction.delete_original_response()
            message = await interaction.followup.send(wait=True, **kwargs)  # type: ignore[call-overload]
    except discord.NotFound:
        log.info("interaction expired before we could reply")
        return None
    view = reply.view
    if message is not None and view is not None and hasattr(view, "message"):
        view.message = message  # type: ignore[attr-defined]
    return message


def error_reply(bot: ShehrazadBot, locale: str, exc: UserError, guild_id: int | None) -> Reply:
    embed = bot.service.error_embed(locale, exc)
    view = bot.service.premium_view(locale, guild_id) if isinstance(exc, PremiumRequired) else None
    return Reply(embed=embed, view=view, ephemeral=True)


async def run(
    bot: ShehrazadBot,
    interaction: discord.Interaction,
    name: str,
    handler: Handler,
    *,
    defer: bool = False,
    defer_ephemeral: bool = False,
) -> None:
    """Run a command handler with consistent UX, logging and metrics."""
    with bind(guild_id=interaction.guild_id) as cid:
        started = time.monotonic()
        outcome = "ok"
        locale = resolve_locale("auto", str(interaction.locale))
        try:
            guild = interaction.guild
            if guild is None or not isinstance(interaction.user, discord.Member):
                raise UserError("error.guild_only", kind="info")
            if (defer or defer_ephemeral) and not interaction.response.is_done():
                await interaction.response.defer(thinking=True, ephemeral=defer_ephemeral)
            actor = await bot.service.actor(guild, interaction.user, interaction.channel_id or 0, str(interaction.locale))
            locale = actor.locale
            reply = await handler(actor)
        except PremiumRequired as exc:
            outcome = "premium"
            reply = error_reply(bot, locale, exc, interaction.guild_id)
        except UserError as exc:
            outcome = "refused"
            reply = error_reply(bot, locale, exc, interaction.guild_id)
        except Exception:
            outcome = "error"
            log.exception("command %s failed", name)
            reply = Reply(embed=ui.error(t(locale, "error.internal", ref=cid)), ephemeral=True)
        finally:
            bot.metrics.commands.labels(name, outcome).inc()
            bot.metrics.command_latency.labels(name).observe(time.monotonic() - started)
        try:
            await send(interaction, reply)
        except discord.HTTPException:
            log.warning("failed to send reply for %s", name, exc_info=True)
