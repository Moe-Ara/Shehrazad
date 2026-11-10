"""Tiers, feature gates and entitlement state.

Discord is the source of truth for paid access. This service mirrors Discord
entitlements (gateway events, interaction payloads, periodic reconciliation)
into the database so checks are fast and survive restarts, and applies
operator-issued complimentary grants. Scope is always per guild.

Event handling is idempotent and safe against duplicates/out-of-order delivery:
  * `deleted` is terminal for gateway/interaction sources;
  * once `ends_at` is known it is never cleared by a gateway/interaction event;
  * a full reconciliation against the List Entitlements API is authoritative,
    but a failed reconciliation never revokes anything (fail-safe).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from .store import AuditRepo, EntitlementRecord, EntitlementRepo, Grant, GrantRepo
from .util import from_iso, to_iso, utcnow

log = logging.getLogger(__name__)


class Tier(StrEnum):
    FREE = "free"
    PREMIUM = "premium"


class Feature(StrEnum):
    SAVED_PLAYLISTS = "saved_playlists"
    STAY_CONNECTED = "stay_connected"  # 24/7
    RESUME_QUEUE = "resume_queue"
    LONG_IDLE_TIMEOUT = "long_idle_timeout"
    LARGE_QUEUE = "large_queue"
    LARGE_PLAYLIST_IMPORT = "large_playlist_import"
    PRIORITY_CAPACITY = "priority_capacity"


PREMIUM_FEATURES = frozenset(Feature)


@dataclass(frozen=True)
class TierLimits:
    max_queue: int
    max_playlist_import: int
    default_idle_seconds: int
    max_idle_seconds: int
    saved_playlists: int
    playlist_tracks: int
    favorites: int
    history_shown: int


LIMITS: dict[Tier, TierLimits] = {
    Tier.FREE: TierLimits(
        max_queue=200,
        max_playlist_import=100,
        default_idle_seconds=300,
        max_idle_seconds=600,
        saved_playlists=0,
        playlist_tracks=0,
        favorites=50,
        history_shown=25,
    ),
    Tier.PREMIUM: TierLimits(
        max_queue=2000,
        max_playlist_import=1000,
        default_idle_seconds=900,
        max_idle_seconds=3600,
        saved_playlists=50,
        playlist_tracks=500,
        favorites=50,
        history_shown=25,
    ),
}


def tier_has(tier: Tier, feature: Feature) -> bool:
    return tier is Tier.PREMIUM or feature not in PREMIUM_FEATURES


class EntitlementLike(Protocol):
    """Shape shared by discord.Entitlement and test doubles."""

    id: int
    sku_id: int
    guild_id: int | None
    user_id: int | None
    deleted: bool
    consumed: bool
    starts_at: datetime | None
    ends_at: datetime | None

    @property
    def type(self) -> Any: ...


@dataclass(frozen=True)
class PremiumStatus:
    tier: Tier
    source: str | None  # "discord" | "grant" | None
    ends_at: datetime | None = None
    grant_reason: str | None = None

    @property
    def is_premium(self) -> bool:
        return self.tier is Tier.PREMIUM


def record_from_entitlement(ent: EntitlementLike, source: str) -> EntitlementRecord:
    ent_type = getattr(ent.type, "value", ent.type)
    return EntitlementRecord(
        id=int(ent.id),
        sku_id=int(ent.sku_id),
        guild_id=int(ent.guild_id) if ent.guild_id else None,
        user_id=int(ent.user_id) if ent.user_id else None,
        type=int(ent_type or 0),
        deleted=bool(ent.deleted),
        consumed=bool(getattr(ent, "consumed", False)),
        starts_at=to_iso(ent.starts_at),
        ends_at=to_iso(ent.ends_at),
        source=source,
    )


def merge_records(old: EntitlementRecord | None, new: EntitlementRecord) -> EntitlementRecord:
    """Combine a stored record with an incoming one, resolving out-of-order events."""
    if old is None or new.source == "reconcile":
        return new
    if old.deleted and not new.deleted:
        return old  # a stale create/update must not resurrect a refunded entitlement
    ends_at = new.ends_at
    if old.ends_at and not new.ends_at:
        ends_at = old.ends_at  # a stale event without an end must not extend access forever
    elif old.ends_at and new.ends_at:
        # End dates only ever move later (renewals); a smaller one is a stale event.
        ends_at = max(old.ends_at, new.ends_at)
    return EntitlementRecord(
        id=new.id,
        sku_id=new.sku_id,
        guild_id=new.guild_id or old.guild_id,
        user_id=new.user_id or old.user_id,
        type=new.type,
        deleted=new.deleted or old.deleted,
        consumed=new.consumed or old.consumed,
        starts_at=new.starts_at or old.starts_at,
        ends_at=ends_at,
        source=new.source,
    )


def record_is_active(rec: EntitlementRecord, now: datetime, sku_ids: frozenset[int]) -> bool:
    if rec.sku_id not in sku_ids or rec.deleted or rec.consumed:
        return False
    starts = from_iso(rec.starts_at)
    ends = from_iso(rec.ends_at)
    if starts is not None and now < starts:
        return False
    return ends is None or now < ends


class EntitlementService:
    def __init__(
        self,
        entitlements: EntitlementRepo,
        grants: GrantRepo,
        audit: AuditRepo,
        *,
        sku_ids: frozenset[int],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._repo = entitlements
        self._grants_repo = grants
        self._audit = audit
        self.sku_ids = sku_ids
        self._clock = clock
        self._records: dict[int, EntitlementRecord] = {}
        self._grants: dict[int, list[Grant]] = {}
        self._lock = asyncio.Lock()
        self.last_reconcile_ok: datetime | None = None
        self.last_reconcile_error: str | None = None
        self._listeners: list[Callable[[int, bool], Any]] = []

    # ---------------------------------------------------------------- loading

    async def load(self) -> None:
        self._records = {r.id: r for r in await self._repo.all()}
        await self.refresh_grants()

    async def refresh_grants(self) -> None:
        grants: dict[int, list[Grant]] = {}
        for grant in await self._grants_repo.all():
            grants.setdefault(grant.guild_id, []).append(grant)
        before = {g for g in self._grants if self._grant_active(g)}
        self._grants = grants
        after = {g for g in self._grants if self._grant_active(g)}
        for guild_id in before ^ after:
            self._notify(guild_id)

    def on_change(self, callback: Callable[[int, bool], Any]) -> None:
        self._listeners.append(callback)

    def _notify(self, guild_id: int) -> None:
        premium = self.status(guild_id).is_premium
        for callback in self._listeners:
            try:
                callback(guild_id, premium)
            except Exception:
                log.exception("premium change listener failed")

    # ---------------------------------------------------------------- queries

    def _grant_active(self, guild_id: int) -> bool:
        now = self._clock()
        return any(g.active(now) for g in self._grants.get(guild_id, []))

    def status(self, guild_id: int | None) -> PremiumStatus:
        if guild_id is None:
            return PremiumStatus(Tier.FREE, None)
        now = self._clock()
        active = [
            r for r in self._records.values()
            if r.guild_id == guild_id and record_is_active(r, now, self.sku_ids)
        ]
        if active:
            ends = [from_iso(r.ends_at) for r in active]
            ends_at = None if any(e is None for e in ends) else max(e for e in ends if e)
            return PremiumStatus(Tier.PREMIUM, "discord", ends_at=ends_at)
        for grant in self._grants.get(guild_id, []):
            if grant.active(now):
                return PremiumStatus(
                    Tier.PREMIUM, "grant", ends_at=from_iso(grant.expires_at), grant_reason=grant.reason
                )
        return PremiumStatus(Tier.FREE, None)

    def tier(self, guild_id: int | None) -> Tier:
        return self.status(guild_id).tier

    def limits(self, guild_id: int | None) -> TierLimits:
        return LIMITS[self.tier(guild_id)]

    def has(self, guild_id: int | None, feature: Feature) -> bool:
        return tier_has(self.tier(guild_id), feature)

    def premium_guild_ids(self) -> set[int]:
        now = self._clock()
        guilds = {
            r.guild_id for r in self._records.values()
            if r.guild_id and record_is_active(r, now, self.sku_ids)
        }
        guilds |= {g for g, grants in self._grants.items() if any(x.active(now) for x in grants)}
        return guilds

    # ---------------------------------------------------------------- updates

    async def apply(self, ent: EntitlementLike, source: str) -> bool:
        """Apply one entitlement observation. Returns True if stored state changed."""
        incoming = record_from_entitlement(ent, source)
        async with self._lock:
            old = self._records.get(incoming.id)
            merged = merge_records(old, incoming)
            if old is not None and _same_state(old, merged):
                return False
            before = self.status(merged.guild_id).is_premium if merged.guild_id else None
            await self._repo.upsert(merged)
            self._records[merged.id] = merged
        log.info(
            "entitlement %s updated via %s",
            merged.id,
            source,
            extra={"sku_id": merged.sku_id, "deleted": merged.deleted, "ends_at": merged.ends_at},
        )
        if merged.guild_id and before != self.status(merged.guild_id).is_premium:
            self._notify(merged.guild_id)
        return True

    async def apply_deleted(self, ent: EntitlementLike) -> bool:
        """ENTITLEMENT_DELETE: refunds, removals, deleted test entitlements."""

        class _Deleted:
            def __init__(self, e: EntitlementLike) -> None:
                self.id, self.sku_id, self.guild_id, self.user_id = e.id, e.sku_id, e.guild_id, e.user_id
                self.type, self.consumed = e.type, getattr(e, "consumed", False)
                self.starts_at, self.ends_at, self.deleted = e.starts_at, e.ends_at, True

        return await self.apply(_Deleted(ent), "gateway")

    async def apply_many(self, ents: Iterable[EntitlementLike], source: str) -> int:
        changed = 0
        for ent in ents:
            if int(ent.sku_id) in self.sku_ids or not self.sku_ids:
                changed += int(await self.apply(ent, source))
        return changed

    async def reconcile(self, fetch: Callable[[], AsyncIterator[EntitlementLike]]) -> bool:
        """Re-sync with the List Entitlements API.

        Entitlements we hold as active but the API no longer returns were
        deleted/refunded while we were offline. If the API call fails part-way
        we change nothing.
        """
        if not self.sku_ids:
            return True
        try:
            seen: list[EntitlementLike] = [ent async for ent in fetch()]
        except Exception as exc:  # network/API failure: keep last known state
            self.last_reconcile_error = type(exc).__name__
            log.warning("entitlement reconcile failed; keeping cached state", exc_info=True)
            return False
        seen_ids = {int(e.id) for e in seen}
        for ent in seen:
            await self.apply(ent, "reconcile")
        now = self._clock()
        for rec in list(self._records.values()):
            if rec.id not in seen_ids and record_is_active(rec, now, self.sku_ids):
                gone = EntitlementRecord(**{**rec.__dict__, "deleted": True, "source": "reconcile"})
                await self._repo.upsert(gone)
                self._records[rec.id] = gone
                log.info("entitlement %s missing from API; marked deleted", rec.id)
                if rec.guild_id:
                    self._notify(rec.guild_id)
        self.last_reconcile_ok = now
        self.last_reconcile_error = None
        return True

    # ---------------------------------------------------------------- grants

    async def grant(self, guild_id: int, *, reason: str, actor: str, expires_at: datetime | None = None) -> int:
        if not reason.strip():
            raise ValueError("a reason is required for audit purposes")
        grant_id = await self._grants_repo.create(guild_id, reason.strip(), actor, expires_at)
        await self._audit.record(actor, "premium.grant", str(guild_id), reason=reason, expires_at=to_iso(expires_at), grant_id=grant_id)
        await self.refresh_grants()
        return grant_id

    async def revoke(self, guild_id: int, *, actor: str, reason: str) -> int:
        count = await self._grants_repo.revoke(guild_id, actor)
        await self._audit.record(actor, "premium.revoke", str(guild_id), reason=reason, revoked=count)
        await self.refresh_grants()
        return count


def _same_state(a: EntitlementRecord, b: EntitlementRecord) -> bool:
    return (
        a.deleted == b.deleted and a.consumed == b.consumed and a.ends_at == b.ends_at
        and a.starts_at == b.starts_at and a.guild_id == b.guild_id and a.sku_id == b.sku_id
    )
