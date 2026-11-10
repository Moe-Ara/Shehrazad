"""/help, /about, /invite, /support, /status, /privacy and /feedback."""

from __future__ import annotations

import io
import json
import logging
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import permissions as perms
from .. import ui
from ..errors import UserError
from ..i18n import resolve_locale, t
from ..util import truncate

if TYPE_CHECKING:
    from ..bot import ShehrazadBot

log = logging.getLogger(__name__)

HELP_CATEGORIES = {
    "playback": ("help.cat_playback", "▶️", ["play", "pause", "resume", "skip", "previous", "seek", "volume", "loop", "nowplaying", "stop", "join", "leave"]),
    "queue": ("help.cat_queue", "📜", ["queue", "remove", "move", "jump", "shuffle", "clear", "playlist", "history", "restore"]),
    "library": ("help.cat_library", "📚", ["library save", "library load", "library list", "library view", "library delete", "library favorites", "library like"]),
    "server": ("help.cat_server", "⚙️", ["setup", "settings view", "settings dj-role", "settings dj-mode", "settings music-channel", "settings stay-connected"]),
    "info": ("help.cat_info", "ℹ️", ["premium", "status", "invite", "support", "privacy", "feedback", "about"]),
}


def help_embed(bot: ShehrazadBot, locale: str, category: str | None) -> discord.Embed:
    if category is None or category not in HELP_CATEGORIES:
        embed = ui.info(f"{ui.ICON['music']} {t(locale, 'help.title')}", t(locale, "help.intro"))
        for key, (label_key, emoji, _cmds) in HELP_CATEGORIES.items():
            embed.add_field(name=f"{emoji} {t(locale, label_key)}", value=t(locale, f"help.blurb_{key}"), inline=False)
        embed.set_footer(text=t(locale, "help.footer"))
        return embed
    label_key, emoji, names = HELP_CATEGORIES[category]
    mentions = bot.command_mentions
    lines = []
    for name in names:
        lines.append(f"{mentions.get(name, f'`/{name}`')} — {t(locale, 'cmd.' + name.replace(' ', '_').replace('-', '_'))}")
    return ui.info(f"{emoji} {t(locale, label_key)}", "\n".join(lines))


class HelpView(discord.ui.View):
    def __init__(self, bot: ShehrazadBot, locale: str) -> None:
        super().__init__(timeout=300)
        self.bot, self.locale = bot, locale
        self.message: discord.Message | None = None
        select: discord.ui.Select[HelpView] = discord.ui.Select(
            placeholder=t(locale, "help.pick"),
            options=[
                discord.SelectOption(label=t(locale, label_key), value=key, emoji=emoji)
                for key, (label_key, emoji, _c) in HELP_CATEGORIES.items()
            ],
        )

        async def picked(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(embed=help_embed(self.bot, self.locale, select.values[0]), view=self)

        select.callback = picked  # type: ignore[method-assign]
        self.add_item(select)
        if bot.config.support_url:
            self.add_item(ui.link_button(t(locale, "btn.support"), bot.config.support_url, "🛟"))

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class FeedbackModal(discord.ui.Modal):
    def __init__(self, bot: ShehrazadBot, locale: str, kind: str) -> None:
        super().__init__(title=t(locale, "feedback.title"), timeout=600)
        self.bot, self.locale, self.kind = bot, locale, kind
        self.body: discord.ui.TextInput[FeedbackModal] = discord.ui.TextInput(
            label=t(locale, "feedback.label"),
            style=discord.TextStyle.paragraph,
            min_length=10,
            max_length=1000,
            placeholder=t(locale, "feedback.placeholder"),
        )
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.body.value).strip()
        if not self.bot.feedback_limiter.allow(("feedback", interaction.user.id)):
            await interaction.response.send_message(embed=ui.warning(t(self.locale, "feedback.too_often")), ephemeral=True)
            return
        feedback_id = await self.bot.store.feedback.add(interaction.guild_id, interaction.user.id, self.kind, text)
        channel_id = self.bot.config.feedback_channel_id
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.abc.Messageable):
            embed = discord.Embed(
                title=f"{self.kind.title()} #{feedback_id}",
                description=truncate(text, 1000),
                color=ui.COLOR_NEUTRAL,
            )
            embed.add_field(name="Guild", value=str(interaction.guild_id or "DM"))
            embed.add_field(name="User", value=str(interaction.user.id))
            embed.set_footer(text=f"v{self.bot.version}")
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.warning("could not forward feedback to channel")
        await interaction.response.send_message(embed=ui.success(t(self.locale, "feedback.thanks")), ephemeral=True)


class Info(commands.Cog):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="How to use the bot.")
    async def help(self, interaction: discord.Interaction) -> None:
        locale = await self._locale(interaction)
        view = HelpView(self.bot, locale)
        await interaction.response.send_message(embed=help_embed(self.bot, locale, None), view=view, ephemeral=True)
        view.message = await interaction.original_response()

    async def _locale(self, interaction: discord.Interaction) -> str:
        setting = "auto"
        if interaction.guild_id:
            setting = (await self.bot.store.settings.get(interaction.guild_id)).language
        return resolve_locale(setting, str(interaction.locale))

    @app_commands.command(name="about", description="About this bot.")
    async def about(self, interaction: discord.Interaction) -> None:
        locale = await self._locale(interaction)
        embed = ui.info(f"{ui.ICON['music']} {ui.BRAND_NAME}", t(locale, "about.body"))
        embed.add_field(name=t(locale, "about.version"), value=self.bot.version)
        embed.add_field(name=t(locale, "about.servers"), value=str(len(self.bot.guilds)))
        view = self._links_view(locale)
        if view is not None:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    def _links_view(self, locale: str) -> discord.ui.View | None:
        view = discord.ui.View()
        cfg = self.bot.config
        if self.bot.application_id:
            view.add_item(ui.link_button(t(locale, "btn.invite"), perms.invite_url(self.bot.application_id), "➕"))
        for url, key, emoji in (
            (cfg.support_url, "btn.support", "🛟"),
            (cfg.website_url, "btn.website", "🌐"),
            (cfg.terms_url, "btn.terms", "📄"),
            (cfg.privacy_url, "btn.privacy", "🔒"),
        ):
            if url:
                view.add_item(ui.link_button(t(locale, key), url, emoji))
        return view if view.children else None

    @app_commands.command(name="invite", description="Add the bot to another server.")
    async def invite(self, interaction: discord.Interaction) -> None:
        locale = await self._locale(interaction)
        if not self.bot.application_id:
            await interaction.response.send_message(embed=ui.warning(t(locale, "error.internal", ref="-")), ephemeral=True)
            return
        embed = ui.info(f"➕ {t(locale, 'invite.title')}", t(locale, "invite.body"))
        view = discord.ui.View()
        view.add_item(ui.link_button(t(locale, "btn.invite"), perms.invite_url(self.bot.application_id), "➕"))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="support", description="Get help from the team behind the bot.")
    async def support(self, interaction: discord.Interaction) -> None:
        locale = await self._locale(interaction)
        cfg = self.bot.config
        if cfg.support_url:
            view = discord.ui.View()
            view.add_item(ui.link_button(t(locale, "btn.support"), cfg.support_url, "🛟"))
            await interaction.response.send_message(embed=ui.info(f"🛟 {t(locale, 'support.title')}", t(locale, "support.body")), view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=ui.info(f"🛟 {t(locale, 'support.title')}", t(locale, "support.no_server")), ephemeral=True)

    @app_commands.command(name="status", description="Bot health and this server's player status.")
    async def status(self, interaction: discord.Interaction) -> None:
        locale = await self._locale(interaction)
        bot = self.bot
        uptime = int(time.monotonic() - bot.started_monotonic)
        embed = ui.info(f"📡 {t(locale, 'status.title')}")
        embed.add_field(name=t(locale, "status.latency"), value=f"{round(bot.latency * 1000)} ms")
        embed.add_field(name=t(locale, "status.uptime"), value=f"{uptime // 3600}h {uptime % 3600 // 60}m")
        embed.add_field(name=t(locale, "status.sessions"), value=str(bot.manager.active_count))
        providers = ", ".join(
            f"{name} {'🟢' if bot.flags.enabled(f'provider.{name}') else '🔴'}" for name in ("youtube", "direct")
        )
        embed.add_field(name=t(locale, "status.sources"), value=providers, inline=False)
        if interaction.guild_id:
            player = bot.manager.get(interaction.guild_id)
            state = t(locale, f"status.player_{player.state.value}") if player else t(locale, "status.player_none")
            tier = bot.premium.status(interaction.guild_id)
            embed.add_field(name=t(locale, "status.this_server"), value=f"{state} • {'✨ Premium' if tier.is_premium else 'Free'}", inline=False)
            if isinstance(interaction.user, discord.Member) and interaction.guild is not None:
                actor = await bot.service.actor(interaction.guild, interaction.user, interaction.channel_id or 0, str(interaction.locale))
                from .settings import diagnostics

                text = interaction.channel if isinstance(interaction.channel, discord.abc.GuildChannel) else None
                problems = diagnostics(actor, actor.voice_channel, text)
                if problems:
                    embed.add_field(name=t(locale, "setup.checks"), value="\n".join(problems), inline=False)
        embed.set_footer(text=f"v{bot.version}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="privacy", description="What data the bot stores, and how to export or delete it.")
    async def privacy(self, interaction: discord.Interaction) -> None:
        locale = await self._locale(interaction)
        cfg = self.bot.config
        embed = ui.info(
            f"🔒 {t(locale, 'privacy.title')}",
            t(locale, "privacy.body", history_days=cfg.history_retention_days, departed_days=cfg.departed_guild_retention_days),
        )
        view = PrivacyView(self.bot, locale, interaction.user.id)
        if cfg.privacy_url:
            view.add_item(ui.link_button(t(locale, "btn.privacy"), cfg.privacy_url, "📄"))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="feedback", description="Report a bug or suggest an idea.")
    @app_commands.choices(kind=[
        app_commands.Choice(name="Bug report", value="bug"),
        app_commands.Choice(name="Idea", value="idea"),
        app_commands.Choice(name="Other", value="other"),
    ])
    async def feedback(self, interaction: discord.Interaction, kind: app_commands.Choice[str]) -> None:
        locale = await self._locale(interaction)
        if not self.bot.flags.enabled("feature.feedback"):
            await interaction.response.send_message(embed=ui.warning(t(locale, "feature.disabled")), ephemeral=True)
            return
        await interaction.response.send_modal(FeedbackModal(self.bot, locale, kind.value))


class PrivacyView(discord.ui.View):
    def __init__(self, bot: ShehrazadBot, locale: str, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.bot, self.locale, self.owner_id = bot, locale, owner_id
        buttons = [
            ("privacy.export_mine", "📦", self._export_user, discord.ButtonStyle.secondary),
            ("privacy.delete_mine", "🗑️", self._delete_user, discord.ButtonStyle.danger),
            ("privacy.export_server", "📦", self._export_guild, discord.ButtonStyle.secondary),
            ("privacy.delete_server", "🗑️", self._delete_guild, discord.ButtonStyle.danger),
        ]
        for key, emoji, callback, style in buttons:
            button: discord.ui.Button[PrivacyView] = discord.ui.Button(label=t(locale, key), emoji=emoji, style=style)
            button.callback = callback  # type: ignore[method-assign]
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=ui.warning(t(self.locale, "picker.not_yours")), ephemeral=True)
            return False
        if not self.bot.feedback_limiter.allow(("privacy", interaction.user.id)):
            await interaction.response.send_message(embed=ui.warning(t(self.locale, "error.rate_limited")), ephemeral=True)
            return False
        return True

    def _require_manager(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not perms.is_manager(interaction.user):
            raise UserError("perm.manage_guild", kind="warning")

    async def _send_json(self, interaction: discord.Interaction, data: dict[str, object], filename: str) -> None:
        payload = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode()
        await interaction.response.send_message(
            embed=ui.success(t(self.locale, "privacy.exported")),
            file=discord.File(io.BytesIO(payload), filename=filename),
            ephemeral=True,
        )

    async def _confirm(self, interaction: discord.Interaction, body_key: str) -> discord.Interaction | None:
        confirm = ui.ConfirmView(locale=self.locale, owner_id=interaction.user.id, danger_label=t(self.locale, "btn.delete"))
        await interaction.response.send_message(embed=ui.warning(t(self.locale, body_key)), view=confirm, ephemeral=True)
        await confirm.wait()
        if not confirm.confirmed or confirm.interaction is None:
            if confirm.interaction is not None:
                await confirm.interaction.response.edit_message(embed=ui.info(None, t(self.locale, "common.cancelled")), view=None)
            return None
        return confirm.interaction

    async def _export_user(self, interaction: discord.Interaction) -> None:
        data = await self.bot.store.rights.export_user(interaction.user.id)
        await self._send_json(interaction, data, f"my-data-{interaction.user.id}.json")

    async def _delete_user(self, interaction: discord.Interaction) -> None:
        confirmed = await self._confirm(interaction, "privacy.confirm_mine")
        if confirmed is None:
            return
        counts = await self.bot.store.rights.delete_user(interaction.user.id)
        await self.bot.store.audit.record(f"user:{interaction.user.id}", "privacy.delete_user", str(interaction.user.id), **counts)
        await confirmed.response.edit_message(embed=ui.success(t(self.locale, "privacy.deleted")), view=None)

    async def _export_guild(self, interaction: discord.Interaction) -> None:
        try:
            self._require_manager(interaction)
        except UserError as exc:
            await interaction.response.send_message(embed=self.bot.service.error_embed(self.locale, exc), ephemeral=True)
            return
        assert interaction.guild is not None
        data = await self.bot.store.rights.export_guild(interaction.guild.id)
        await self._send_json(interaction, data, f"server-data-{interaction.guild.id}.json")

    async def _delete_guild(self, interaction: discord.Interaction) -> None:
        try:
            self._require_manager(interaction)
        except UserError as exc:
            await interaction.response.send_message(embed=self.bot.service.error_embed(self.locale, exc), ephemeral=True)
            return
        assert interaction.guild is not None
        confirmed = await self._confirm(interaction, "privacy.confirm_server")
        if confirmed is None:
            return
        counts = await self.bot.store.rights.delete_guild(interaction.guild.id)
        self.bot.store.settings.forget(interaction.guild.id)
        await self.bot.store.audit.record(f"user:{interaction.user.id}", "privacy.delete_guild", str(interaction.guild.id), **counts)
        await confirmed.response.edit_message(embed=ui.success(t(self.locale, "privacy.deleted")), view=None)


async def setup(bot: ShehrazadBot) -> None:
    await bot.add_cog(Info(bot))
