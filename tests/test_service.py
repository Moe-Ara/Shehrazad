"""Service-level behaviour with real stores/premium and fake Discord objects."""

from types import SimpleNamespace

import discord
import pytest

from musicbot.bot import Store
from musicbot.config import Settings
from musicbot.errors import PremiumRequired, UserError
from musicbot.ops import FeatureFlags, Metrics
from musicbot.player import GuildPlayer
from musicbot.premium import EntitlementService
from musicbot.service import MusicService
from tests.conftest import FakeHooks, FakeRegistry, FakeSource, FakeVoice, make_track, settle

GUILD = 1
VOICE_CHANNEL = 10
DJ_ROLE = 99


class FakeVoiceChannel:
    def __init__(self, cid=VOICE_CHANNEL, perms=None):
        self.id = cid
        self.mention = f"<#{cid}>"
        self.members = []
        self._perms = perms or discord.Permissions(view_channel=True, connect=True, speak=True)

    def permissions_for(self, _member):
        return self._perms


class FakeManager:
    def __init__(self, bot):
        self.bot = bot
        self.players = {}
        self.voices = {}

    def get(self, guild_id):
        p = self.players.get(guild_id)
        return p if p and not p.closed else None

    async def get_or_create(self, guild, text_channel_id):
        existing = self.get(guild.id)
        if existing:
            return existing
        p = GuildPlayer(guild.id, registry=self.bot.registry, hooks=FakeHooks(), idle_timeout=lambda: None,
                        max_queue=lambda: self.bot.premium.limits(guild.id).max_queue, source_factory=FakeSource)
        self.players[guild.id] = p
        return p

    async def connect(self, player, channel):
        if player.voice is None:
            voice = FakeVoice(channel.id)
            voice.channel = channel
            player.attach(voice)


@pytest.fixture
async def env(db, clock):
    store = Store.create(db)
    bot = SimpleNamespace()
    bot.store = store
    bot.config = Settings(token="x", premium_sku_ids=frozenset({555}))
    bot.premium = EntitlementService(store.entitlements, store.grants, store.audit, sku_ids=frozenset({555}), clock=clock)
    await bot.premium.load()
    bot.flags = FeatureFlags(store.flags)
    bot.registry = FakeRegistry()
    bot.metrics = Metrics()
    bot.manager = FakeManager(bot)
    svc = MusicService(bot)
    bot.service = svc
    channel = FakeVoiceChannel()
    guild = SimpleNamespace(id=GUILD, me=object(), preferred_locale="en-US", voice_client=None)

    def member(uid, *, manage=False, roles=(), in_voice=True):
        perms = discord.Permissions.none()
        perms.manage_guild = manage
        return SimpleNamespace(
            id=uid, bot=False, guild_permissions=perms, roles=[SimpleNamespace(id=r) for r in roles],
            voice=SimpleNamespace(channel=channel) if in_voice else None,
        )

    async def actor(m):
        return await svc.actor(guild, m, 500, "en-US")

    yield SimpleNamespace(bot=bot, svc=svc, channel=channel, guild=guild, member=member, actor=actor)
    for p in list(bot.manager.players.values()):
        await p.shutdown("test")


def listeners(env, n):
    env.channel.members = [SimpleNamespace(bot=False) for _ in range(n)] + [SimpleNamespace(bot=True)]


async def test_play_requires_voice_and_starts(env):
    with pytest.raises(UserError) as exc:
        await env.svc.play(await env.actor(env.member(1, in_voice=False)), "song")
    assert exc.value.key == "voice.join_first"
    reply = await env.svc.play(await env.actor(env.member(1)), "song")
    await settle()
    assert "Starting" in reply.embed.description
    assert env.bot.manager.get(GUILD).current.requester_id == 1


async def test_bot_missing_voice_permissions_is_explained(env):
    env.channel._perms = discord.Permissions(view_channel=True, connect=True, speak=False)
    with pytest.raises(UserError) as exc:
        await env.svc.play(await env.actor(env.member(1)), "song")
    assert exc.value.key == "perm.bot_missing" and "Speak" in exc.value.params["permissions"]


async def test_dj_only_requests(env):
    await env.bot.store.settings.update(GUILD, request_mode="dj", dj_role_id=DJ_ROLE)
    with pytest.raises(UserError) as exc:
        await env.svc.play(await env.actor(env.member(1)), "song")
    assert exc.value.key == "perm.requests_dj_only"
    await env.svc.play(await env.actor(env.member(2, roles=[DJ_ROLE])), "song")


async def test_controls_require_same_channel_and_dj_mode(env):
    await env.svc.play(await env.actor(env.member(1)), "song")
    await settle()
    with pytest.raises(UserError) as exc:
        await env.svc.pause(await env.actor(env.member(2, in_voice=False)))
    assert exc.value.key == "voice.join_first"
    await env.bot.store.settings.update(GUILD, dj_mode=True, dj_role_id=DJ_ROLE)
    with pytest.raises(UserError) as exc:
        await env.svc.pause(await env.actor(env.member(2)))
    assert exc.value.key == "perm.dj_only"
    await env.svc.pause(await env.actor(env.member(1)))  # requester of the current track may
    await env.svc.resume(await env.actor(env.member(3, roles=[DJ_ROLE])))


async def test_vote_skip(env):
    await env.svc.play(await env.actor(env.member(1)), "song one")
    await env.svc.play(await env.actor(env.member(1)), "song two")
    await settle()
    await env.bot.store.settings.update(GUILD, dj_mode=True, dj_role_id=DJ_ROLE)
    listeners(env, 4)
    reply = await env.svc.skip(await env.actor(env.member(2)))
    assert "1/2" in reply.embed.description
    reply = await env.svc.skip(await env.actor(env.member(2)))  # same user twice counts once
    assert "1/2" in reply.embed.description
    reply = await env.svc.skip(await env.actor(env.member(3)))
    await settle()
    assert "Vote passed" in reply.embed.description
    assert env.bot.manager.get(GUILD).current.title == "Track song two"


async def test_skip_disabled_votes_in_dj_mode(env):
    await env.svc.play(await env.actor(env.member(1)), "song")
    await settle()
    await env.bot.store.settings.update(GUILD, dj_mode=True, vote_skip=False, dj_role_id=DJ_ROLE)
    listeners(env, 3)
    with pytest.raises(UserError) as exc:
        await env.svc.skip(await env.actor(env.member(2)))
    assert exc.value.key == "perm.dj_only"


async def test_saved_playlists_are_premium_gated_per_guild(env):
    await env.svc.play(await env.actor(env.member(1)), "song")
    await settle()
    with pytest.raises(PremiumRequired) as exc:
        await env.svc.library_save(await env.actor(env.member(1)), "Mix")
    assert exc.value.feature_key == "feature.saved_playlists"
    await env.bot.premium.grant(GUILD, reason="test", actor="op")
    reply = await env.svc.library_save(await env.actor(env.member(1)), "Mix")
    assert "Saved" in reply.embed.description
    assert (await env.svc.library_list(await env.actor(env.member(1)))).embed.description.count("Mix") == 1


async def test_premium_kill_switch_overrides_entitlement(env):
    await env.bot.premium.grant(GUILD, reason="test", actor="op")
    await env.bot.store.flags.set("feature.saved_playlists", False, "op")
    await env.bot.flags.refresh()
    with pytest.raises(UserError) as exc:
        await env.svc.library_save(await env.actor(env.member(1)), "Mix")
    assert exc.value.key == "feature.disabled"


async def test_queue_full_upsells_only_on_free(env, monkeypatch):
    from musicbot import premium as premium_mod
    from musicbot.premium import LIMITS, Tier, TierLimits

    small = TierLimits(**{**LIMITS[Tier.FREE].__dict__, "max_queue": 2})
    monkeypatch.setitem(premium_mod.LIMITS, Tier.FREE, small)
    a = await env.actor(env.member(1))
    for i in range(3):  # 1 playing + 2 waiting fills a 2-slot queue
        await env.svc.play(a, f"song {i}")
        await settle()
    with pytest.raises(UserError) as exc:
        await env.svc.play(a, "one too many")
    assert exc.value.key == "queue.full_upsell"


async def test_favorites_and_history_pickers(env):
    a = await env.actor(env.member(1))
    empty = await env.svc.favorites(a)
    assert empty.ephemeral and "haven't liked" in empty.embed.description
    await env.svc.play(a, "song")
    await settle()
    reply = await env.svc.favorite_current(a)
    assert "Added" in reply.embed.description
    again = await env.svc.favorite_current(a)
    assert "already" in again.embed.description
    picker = await env.svc.favorites(a)
    assert picker.view is not None and picker.ephemeral


async def test_restore_requires_premium(env):
    with pytest.raises(PremiumRequired):
        await env.svc.restore(await env.actor(env.member(1)))
    await env.bot.premium.grant(GUILD, reason="t", actor="op")
    with pytest.raises(UserError) as exc:
        await env.svc.restore(await env.actor(env.member(1)))
    assert exc.value.key == "restore.nothing"
    await env.bot.store.snapshots.save(GUILD, VOICE_CHANNEL, 5, {"current": make_track(7).to_dict(), "upcoming": [make_track(8).to_dict()]})
    reply = await env.svc.restore(await env.actor(env.member(1)))
    assert "2" in reply.embed.description


async def test_premium_view_only_when_selling_and_not_premium(env):
    assert env.svc.premium_view("en", GUILD) is not None
    await env.bot.store.flags.set("premium.sales", False, "op")
    await env.bot.flags.refresh()
    assert env.svc.premium_view("en", GUILD) is None
    await env.bot.store.flags.set("premium.sales", True, "op")
    await env.bot.flags.refresh()
    await env.bot.premium.grant(GUILD, reason="t", actor="op")
    assert env.svc.premium_view("en", GUILD) is None


async def test_error_embeds_never_leak_internals(env):
    embed = env.svc.error_embed("en", UserError("source.provider_down", kind="warning"))
    assert "Traceback" not in embed.description and "music source" in embed.description
