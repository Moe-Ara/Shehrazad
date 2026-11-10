from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import pytest

from musicbot.premium import LIMITS, EntitlementService, Feature, Tier, tier_has
from musicbot.store import AuditRepo, EntitlementRepo, GrantRepo

SKU = 1111
OTHER_SKU = 2222
GUILD_A, GUILD_B = 100, 200


@dataclass(frozen=True)
class Ent:
    id: int
    sku_id: int = SKU
    guild_id: int | None = GUILD_A
    user_id: int | None = None
    type: int = 8
    deleted: bool = False
    consumed: bool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None


@pytest.fixture
async def service(db, clock):
    svc = EntitlementService(EntitlementRepo(db), GrantRepo(db), AuditRepo(db), sku_ids=frozenset({SKU}), clock=clock)
    await svc.load()
    return svc


async def test_purchase_grants_premium_to_that_guild_only(service):
    assert await service.apply(Ent(1), "gateway")
    assert service.tier(GUILD_A) is Tier.PREMIUM
    assert service.tier(GUILD_B) is Tier.FREE
    assert service.tier(None) is Tier.FREE
    assert service.has(GUILD_A, Feature.SAVED_PLAYLISTS)
    assert not service.has(GUILD_B, Feature.SAVED_PLAYLISTS)
    assert service.limits(GUILD_B) == LIMITS[Tier.FREE]


async def test_user_scoped_or_other_sku_entitlements_do_not_unlock_a_guild(service):
    await service.apply(Ent(2, guild_id=None, user_id=5), "gateway")
    await service.apply(Ent(3, sku_id=OTHER_SKU), "gateway")
    assert service.tier(GUILD_A) is Tier.FREE


async def test_duplicate_events_are_idempotent(service):
    assert await service.apply(Ent(1), "gateway") is True
    assert await service.apply(Ent(1), "gateway") is False
    assert await service.apply(Ent(1), "interaction") is False


async def test_subscription_end_and_expiry(service, clock):
    await service.apply(Ent(1), "gateway")
    ends = clock.now + timedelta(days=3)
    await service.apply(Ent(1, ends_at=ends), "gateway")  # ENTITLEMENT_UPDATE when the sub ends
    status = service.status(GUILD_A)
    assert status.is_premium and status.ends_at == ends
    clock.now = ends + timedelta(seconds=1)
    assert service.tier(GUILD_A) is Tier.FREE


async def test_late_create_does_not_extend_an_ended_entitlement(service, clock):
    ends = clock.now + timedelta(days=1)
    await service.apply(Ent(1, ends_at=ends), "gateway")
    await service.apply(Ent(1, ends_at=None), "gateway")  # stale CREATE delivered late
    clock.now = ends + timedelta(minutes=1)
    assert service.tier(GUILD_A) is Tier.FREE


async def test_refund_delete_is_terminal_even_if_create_arrives_later(service):
    await service.apply_deleted(Ent(1))  # DELETE (refund) arrives first
    await service.apply(Ent(1), "gateway")  # stale CREATE
    assert service.tier(GUILD_A) is Tier.FREE


async def test_refund_revokes_active_premium(service):
    await service.apply(Ent(1), "gateway")
    assert service.tier(GUILD_A) is Tier.PREMIUM
    await service.apply_deleted(Ent(1))
    assert service.tier(GUILD_A) is Tier.FREE


async def test_future_start_and_consumed(service, clock):
    await service.apply(Ent(1, starts_at=clock.now + timedelta(hours=1)), "gateway")
    assert service.tier(GUILD_A) is Tier.FREE
    clock.now += timedelta(hours=2)
    assert service.tier(GUILD_A) is Tier.PREMIUM
    await service.apply(Ent(4, guild_id=GUILD_B, consumed=True), "gateway")
    assert service.tier(GUILD_B) is Tier.FREE


async def test_test_entitlement_without_dates_is_perpetual(service, clock):
    await service.apply(Ent(9, type=4), "gateway")  # test entitlement: no starts_at/ends_at
    clock.now += timedelta(days=3650)
    assert service.tier(GUILD_A) is Tier.PREMIUM


async def test_state_survives_restart(db, clock, service):
    await service.apply(Ent(1), "gateway")
    fresh = EntitlementService(EntitlementRepo(db), GrantRepo(db), AuditRepo(db), sku_ids=frozenset({SKU}), clock=clock)
    await fresh.load()
    assert fresh.tier(GUILD_A) is Tier.PREMIUM


async def test_reconcile_marks_missing_entitlements_deleted(service):
    await service.apply(Ent(1), "gateway")
    await service.apply(Ent(2, guild_id=GUILD_B), "gateway")

    async def fetch():
        yield Ent(2, guild_id=GUILD_B)

    assert await service.reconcile(fetch) is True
    assert service.tier(GUILD_A) is Tier.FREE  # refunded while we were offline
    assert service.tier(GUILD_B) is Tier.PREMIUM


async def test_reconcile_failure_changes_nothing(service):
    await service.apply(Ent(1), "gateway")

    async def broken():
        yield Ent(5, guild_id=GUILD_B)
        raise ConnectionError("discord down")

    assert await service.reconcile(broken) is False
    assert service.tier(GUILD_A) is Tier.PREMIUM
    assert service.tier(GUILD_B) is Tier.FREE
    assert service.last_reconcile_error == "ConnectionError"


async def test_reconcile_is_authoritative_over_stale_local_end(service, clock):
    ends = clock.now + timedelta(hours=1)
    await service.apply(Ent(1, ends_at=ends), "gateway")

    async def fetch():
        yield Ent(1, ends_at=None)  # API says active with no end (e.g. resumed)

    await service.reconcile(fetch)
    clock.now = ends + timedelta(hours=1)
    assert service.tier(GUILD_A) is Tier.PREMIUM


async def test_grants_need_reason_are_audited_and_revocable(service, db):
    with pytest.raises(ValueError):
        await service.grant(GUILD_A, reason="  ", actor="operator:test")
    await service.grant(GUILD_A, reason="owner's server", actor="operator:test")
    status = service.status(GUILD_A)
    assert status.is_premium and status.source == "grant"
    assert service.tier(GUILD_B) is Tier.FREE
    log = await AuditRepo(db).recent()
    assert log[0]["action"] == "premium.grant" and log[0]["actor"] == "operator:test"
    assert await service.revoke(GUILD_A, actor="operator:test", reason="test") == 1
    assert service.tier(GUILD_A) is Tier.FREE


async def test_expiring_grant(service, clock):
    await service.grant(GUILD_A, reason="trial", actor="op", expires_at=clock.now + timedelta(days=7))
    assert service.tier(GUILD_A) is Tier.PREMIUM
    clock.now += timedelta(days=8)
    assert service.tier(GUILD_A) is Tier.FREE


async def test_grant_from_another_process_applies_without_restart(db, clock, service):
    changes = []
    service.on_change(lambda guild, premium: changes.append((guild, premium)))
    cli_side = EntitlementService(EntitlementRepo(db), GrantRepo(db), AuditRepo(db), sku_ids=frozenset({SKU}), clock=clock)
    await cli_side.grant(GUILD_B, reason="partner", actor="op")
    assert service.tier(GUILD_B) is Tier.FREE  # not yet refreshed
    await service.refresh_grants()  # the bot polls this every 60 s
    assert service.tier(GUILD_B) is Tier.PREMIUM
    assert (GUILD_B, True) in changes


def test_free_tier_is_usable():
    free = LIMITS[Tier.FREE]
    assert free.max_queue >= 100 and free.max_playlist_import >= 50 and free.favorites > 0
    assert not tier_has(Tier.FREE, Feature.SAVED_PLAYLISTS)
    assert tier_has(Tier.PREMIUM, Feature.STAY_CONNECTED)


def test_ent_helper_is_frozen():
    assert replace(Ent(1), deleted=True).deleted
