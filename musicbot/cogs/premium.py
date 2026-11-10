"""/premium: status, benefits, upgrade and management."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import ui
from ..i18n import t
from ..interactions import run
from ..premium import LIMITS, Tier
from ..service import Actor, Reply

if TYPE_CHECKING:
    from ..bot import ShehrazadBot


def benefits_text(locale: str) -> str:
    free, prem = LIMITS[Tier.FREE], LIMITS[Tier.PREMIUM]
    rows = [
        t(locale, "premium.row_core"),
        t(locale, "premium.row_queue", free=free.max_queue, premium=prem.max_queue),
        t(locale, "premium.row_import", free=free.max_playlist_import, premium=prem.max_playlist_import),
        t(locale, "premium.row_playlists", premium=prem.saved_playlists),
        t(locale, "premium.row_247"),
        t(locale, "premium.row_resume"),
        t(locale, "premium.row_idle", free=free.max_idle_seconds // 60, premium=prem.max_idle_seconds // 60),
        t(locale, "premium.row_priority"),
    ]
    return "\n".join(rows)


class PremiumCog(commands.Cog, name="Premium"):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot

    @app_commands.command(name="premium", description="See Premium benefits and this server's subscription status.")
    @app_commands.guild_only()
    async def premium(self, interaction: discord.Interaction) -> None:
        async def handler(actor: Actor) -> Reply:
            loc = actor.locale
            status = self.bot.premium.status(actor.guild.id)
            config = self.bot.config
            embed = discord.Embed(title=f"{ui.ICON['premium']} {t(loc, 'premium.title')}", color=ui.COLOR_PREMIUM)
            if status.is_premium:
                if status.source == "grant":
                    state = t(loc, "premium.status_grant")
                elif status.ends_at:
                    state = t(loc, "premium.status_ending", date=discord.utils.format_dt(status.ends_at, "D"))
                else:
                    state = t(loc, "premium.status_active")
            else:
                state = t(loc, "premium.status_free")
            embed.description = f"**{t(loc, 'premium.status')}:** {state}\n\n{benefits_text(loc)}"
            if not status.is_premium and not config.premium_configured:
                embed.add_field(name="​", value=t(loc, "premium.not_on_sale"), inline=False)
            elif status.is_premium and status.source == "discord":
                embed.add_field(name="​", value=t(loc, "premium.manage_hint"), inline=False)
            embed.set_footer(text=t(loc, "premium.footer"))
            view = self.bot.service.premium_view(loc, actor.guild.id) or discord.ui.View()
            if config.support_url:
                view.add_item(ui.link_button(t(loc, "btn.support"), config.support_url, "🛟"))
            return Reply(embed=embed, view=view if view.children else None, ephemeral=True)

        await run(self.bot, interaction, "premium", handler)


async def setup(bot: ShehrazadBot) -> None:
    await bot.add_cog(PremiumCog(bot))
