"""/setup wizard and /settings: per-server configuration (Manage Server only)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from .. import permissions as perms
from .. import ui
from ..errors import PremiumRequired, UserError
from ..i18n import t
from ..interactions import run
from ..premium import Feature
from ..service import Actor, Reply
from ..store import GuildSettings

if TYPE_CHECKING:
    from ..bot import ShehrazadBot


def settings_embed(bot: ShehrazadBot, actor: Actor, settings: GuildSettings) -> discord.Embed:
    loc = actor.locale
    status = bot.premium.status(actor.guild.id)
    yes, no = t(loc, "common.on"), t(loc, "common.off")
    limits = bot.premium.limits(actor.guild.id)
    idle = settings.idle_timeout_seconds or limits.default_idle_seconds
    embed = ui.info(f"{ui.ICON['settings']} {t(loc, 'settings.title')}")
    rows = [
        (t(loc, "settings.music_channel"), f"<#{settings.music_channel_id}>" if settings.music_channel_id else t(loc, "settings.any_channel")),
        (t(loc, "settings.dj_role"), f"<@&{settings.dj_role_id}>" if settings.dj_role_id else t(loc, "settings.none")),
        (t(loc, "settings.dj_mode"), yes if settings.dj_mode else no),
        (t(loc, "settings.requests"), t(loc, f"settings.requests_{settings.request_mode}")),
        (t(loc, "settings.vote_skip"), f"{yes} ({round(settings.vote_skip_ratio * 100)}%)" if settings.vote_skip else no),
        (t(loc, "settings.default_volume"), f"{settings.default_volume}%"),
        (t(loc, "settings.idle_timeout"), t(loc, "settings.minutes", count=min(idle, limits.max_idle_seconds) // 60)),
        (t(loc, "settings.stay_connected"), (yes if settings.stay_connected else no) + ("" if status.is_premium else " ✨")),
    ]
    embed.description = "\n".join(f"**{name}:** {value}" for name, value in rows)
    embed.set_footer(text=t(loc, "settings.footer_premium" if status.is_premium else "settings.footer_free"))
    return embed


def diagnostics(actor: Actor, voice: discord.abc.GuildChannel | None, text: discord.abc.GuildChannel | None) -> list[str]:
    me = actor.guild.me
    problems = []
    if text is not None:
        missing = perms.missing(text.permissions_for(me), perms.TEXT_PERMISSIONS)
        if missing:
            problems.append(t(actor.locale, "setup.missing_text", channel=text.mention, permissions=", ".join(missing)))
    if voice is not None:
        missing = perms.missing(voice.permissions_for(me), perms.VOICE_PERMISSIONS)
        if missing:
            problems.append(t(actor.locale, "setup.missing_voice", channel=voice.mention, permissions=", ".join(missing)))
    return problems


class SetupView(discord.ui.View):
    """Interactive first-run setup: channel, DJ role, DJ mode and vote-skip."""

    def __init__(self, bot: ShehrazadBot, actor: Actor) -> None:
        super().__init__(timeout=600)
        self.bot, self.actor = bot, actor
        self.message: discord.Message | None = None
        loc = actor.locale
        channel_select: discord.ui.ChannelSelect[SetupView] = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text], placeholder=t(loc, "setup.pick_channel"), min_values=0, max_values=1, row=0
        )
        role_select: discord.ui.RoleSelect[SetupView] = discord.ui.RoleSelect(
            placeholder=t(loc, "setup.pick_role"), min_values=0, max_values=1, row=1
        )
        channel_select.callback = self._make(lambda i, s=channel_select: self._save(i, music_channel_id=s.values[0].id if s.values else None))  # type: ignore[method-assign]
        role_select.callback = self._make(lambda i, s=role_select: self._save(i, dj_role_id=s.values[0].id if s.values else None))  # type: ignore[method-assign]
        for item in (channel_select, role_select):
            self.add_item(item)
        self.dj_button: discord.ui.Button[SetupView] = discord.ui.Button(row=2, style=discord.ButtonStyle.secondary)
        self.vote_button: discord.ui.Button[SetupView] = discord.ui.Button(row=2, style=discord.ButtonStyle.secondary)
        done: discord.ui.Button[SetupView] = discord.ui.Button(label=t(loc, "setup.finish"), style=discord.ButtonStyle.success, row=2, emoji="✅")
        self.dj_button.callback = self._make(lambda i: self._save(i, dj_mode=not self.actor.settings.dj_mode))  # type: ignore[method-assign]
        self.vote_button.callback = self._make(lambda i: self._save(i, vote_skip=not self.actor.settings.vote_skip))  # type: ignore[method-assign]
        done.callback = self._finish  # type: ignore[method-assign]
        for button in (self.dj_button, self.vote_button, done):
            self.add_item(button)
        self._label_toggles()

    def _label_toggles(self) -> None:
        loc, s = self.actor.locale, self.actor.settings
        self.dj_button.label = t(loc, "setup.toggle_dj", state=t(loc, "common.on" if s.dj_mode else "common.off"))
        self.vote_button.label = t(loc, "setup.toggle_vote", state=t(loc, "common.on" if s.vote_skip else "common.off"))

    def _make(self, fn: Any) -> Any:
        async def callback(interaction: discord.Interaction) -> None:
            await fn(interaction)

        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.actor.member.id:
            await interaction.response.send_message(embed=ui.warning(t(self.actor.locale, "picker.not_yours")), ephemeral=True)
            return False
        return True

    def render(self) -> discord.Embed:
        embed = settings_embed(self.bot, self.actor, self.actor.settings)
        embed.title = f"{ui.ICON['settings']} {t(self.actor.locale, 'setup.title')}"
        text = self.actor.guild.get_channel(self.actor.settings.music_channel_id or self.actor.channel_id)
        voice = self.actor.voice_channel
        problems = diagnostics(self.actor, voice, text)  # type: ignore[arg-type]
        embed.add_field(
            name=t(self.actor.locale, "setup.checks"),
            value="\n".join(f"{ui.ICON['error']} {p}" for p in problems) if problems else f"{ui.ICON['success']} {t(self.actor.locale, 'setup.all_good')}",
            inline=False,
        )
        return embed

    async def _save(self, interaction: discord.Interaction, **changes: Any) -> None:
        self.actor.settings = await self.bot.store.settings.update(self.actor.guild.id, **changes)
        await self.bot.store.audit.record(f"user:{interaction.user.id}", "settings.update", str(self.actor.guild.id), **changes)
        self._label_toggles()
        await interaction.response.edit_message(embed=self.render(), view=self)

    async def _finish(self, interaction: discord.Interaction) -> None:
        self.actor.settings = await self.bot.store.settings.update(self.actor.guild.id, setup_completed=True)
        embed = self.render()
        embed.title = f"{ui.ICON['success']} {t(self.actor.locale, 'setup.done_title')}"
        embed.description = t(self.actor.locale, "setup.done_body") + "\n\n" + (embed.description or "")
        self.stop()
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class Settings(commands.GroupCog, group_name="settings", group_description="Configure the bot for this server"):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot
        super().__init__()

    async def _update(
        self,
        interaction: discord.Interaction,
        name: str,
        changes: dict[str, Any],
        *,
        feature: Feature | None = None,
        validate: Any = None,
    ) -> None:
        async def handler(actor: Actor) -> Reply:
            if not perms.can_manage_settings(actor.member):
                raise UserError("perm.manage_guild", kind="warning")
            if feature is not None and not self.bot.premium.has(actor.guild.id, feature):
                raise PremiumRequired(f"feature.{feature.value}")
            if validate is not None:
                validate(actor)
            try:
                updated = await self.bot.store.settings.update(actor.guild.id, **changes)
            except ValueError as exc:
                raise UserError("settings.invalid", kind="warning") from exc
            await self.bot.store.audit.record(f"user:{actor.member.id}", "settings.update", str(actor.guild.id), **changes)
            actor.settings = updated
            embed = settings_embed(self.bot, actor, updated)
            embed.title = f"{ui.ICON['success']} {t(actor.locale, 'settings.saved')}"
            return Reply(embed=embed, ephemeral=True)

        await run(self.bot, interaction, f"settings.{name}", handler)

    @app_commands.command(name="view", description="Show this server's settings.")
    @app_commands.guild_only()
    async def view(self, interaction: discord.Interaction) -> None:
        async def handler(actor: Actor) -> Reply:
            return Reply(embed=settings_embed(self.bot, actor, actor.settings), ephemeral=True)

        await run(self.bot, interaction, "settings.view", handler)

    @app_commands.command(name="music-channel", description="Where the player panel is posted (empty = where /play is used).")
    @app_commands.guild_only()
    @app_commands.describe(channel="Text channel for the player panel (leave empty to use wherever /play is run)")
    async def music_channel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        await self._update(interaction, "music_channel", {"music_channel_id": channel.id if channel else None})

    @app_commands.command(name="dj-role", description="Role that can always control playback (empty = none).")
    @app_commands.guild_only()
    @app_commands.describe(role="The DJ role (leave empty to remove it)")
    async def dj_role(self, interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        await self._update(interaction, "dj_role", {"dj_role_id": role.id if role else None})

    @app_commands.command(name="dj-mode", description="Only DJs (and requesters of the current track) can control playback.")
    @app_commands.guild_only()
    @app_commands.describe(enabled="Turn DJ mode on or off")
    async def dj_mode(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self._update(interaction, "dj_mode", {"dj_mode": enabled})

    @app_commands.command(name="requests", description="Who can add songs.")
    @app_commands.choices(who=[app_commands.Choice(name="Everyone", value="everyone"), app_commands.Choice(name="DJs only", value="dj")])
    @app_commands.guild_only()
    @app_commands.describe(who="Who may add songs to the queue")
    async def requests(self, interaction: discord.Interaction, who: app_commands.Choice[str]) -> None:
        await self._update(interaction, "requests", {"request_mode": who.value})

    @app_commands.command(name="vote-skip", description="Let listeners vote to skip, and how many must agree.")
    @app_commands.describe(enabled="Turn vote-skip on or off", percent="Share of listeners needed (10-100)")
    @app_commands.guild_only()
    async def vote_skip(self, interaction: discord.Interaction, enabled: bool, percent: app_commands.Range[int, 10, 100] = 50) -> None:
        await self._update(interaction, "vote_skip", {"vote_skip": enabled, "vote_skip_ratio": percent / 100})

    @app_commands.command(name="default-volume", description="Volume new sessions start at.")
    @app_commands.guild_only()
    @app_commands.describe(percent="Starting volume in percent (1-150)")
    async def default_volume(self, interaction: discord.Interaction, percent: app_commands.Range[int, 1, 150]) -> None:
        await self._update(interaction, "default_volume", {"default_volume": percent})


    @app_commands.command(name="idle-timeout", description="Minutes to wait with an empty queue before leaving.")
    @app_commands.guild_only()
    @app_commands.describe(minutes="Minutes before leaving (Free: up to 10, Premium: up to 60)")
    async def idle_timeout(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 60]) -> None:
        def validate(actor: Actor) -> None:
            limits = self.bot.premium.limits(actor.guild.id)
            if minutes * 60 > limits.max_idle_seconds:
                if not self.bot.premium.status(actor.guild.id).is_premium:
                    raise PremiumRequired("feature.long_idle_timeout")
                raise UserError("settings.idle_too_long", kind="warning", max=limits.max_idle_seconds // 60)

        await self._update(interaction, "idle_timeout", {"idle_timeout_seconds": minutes * 60}, validate=validate)

    @app_commands.command(name="stay-connected", description="24/7: stay in a voice channel even when the queue ends (Premium).")
    @app_commands.describe(enabled="Turn 24/7 on or off", channel="Voice channel to stay in (default: your current one)")
    @app_commands.guild_only()
    async def stay_connected(self, interaction: discord.Interaction, enabled: bool, channel: discord.VoiceChannel | None = None) -> None:
        target = channel.id if channel else None
        if target is None and isinstance(interaction.user, discord.Member) and interaction.user.voice and interaction.user.voice.channel:
            target = interaction.user.voice.channel.id
        changes: dict[str, Any] = {"stay_connected": enabled}
        if enabled:
            changes["stay_channel_id"] = target
        await self._update(interaction, "stay_connected", changes, feature=Feature.STAY_CONNECTED if enabled else None)

    @app_commands.command(name="reset", description="Reset all settings to their defaults.")
    @app_commands.guild_only()
    async def reset(self, interaction: discord.Interaction) -> None:
        async def handler(actor: Actor) -> Reply:
            if not perms.can_manage_settings(actor.member):
                raise UserError("perm.manage_guild", kind="warning")
            updated = await self.bot.store.settings.reset(actor.guild.id)
            await self.bot.store.audit.record(f"user:{actor.member.id}", "settings.reset", str(actor.guild.id))
            actor.settings = updated
            return Reply(embed=settings_embed(self.bot, actor, updated), ephemeral=True)

        await run(self.bot, interaction, "settings.reset", handler)


class Setup(commands.Cog):
    def __init__(self, bot: ShehrazadBot) -> None:
        self.bot = bot

    @app_commands.command(name="setup", description="Set up the bot for this server in under a minute.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction) -> None:
        async def handler(actor: Actor) -> Reply:
            if not perms.can_manage_settings(actor.member):
                raise UserError("perm.manage_guild", kind="warning")
            view = SetupView(self.bot, actor)
            embed = view.render()
            embed.description = t(actor.locale, "setup.intro") + "\n\n" + (embed.description or "")
            return Reply(embed=embed, view=view, ephemeral=True)

        await run(self.bot, interaction, "setup", handler)


async def setup(bot: ShehrazadBot) -> None:
    await bot.add_cog(Settings(bot))
    await bot.add_cog(Setup(bot))
