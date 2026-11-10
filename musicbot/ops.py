"""Operational plumbing: feature flags / kill switches, rate limiting, metrics."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from .store import FlagRepo

log = logging.getLogger(__name__)

# Flags default to enabled; operators flip them with `python -m musicbot.cli flag ...`.
KNOWN_FLAGS = {
    "provider.youtube": "YouTube/SoundCloud lookups and playback",
    "provider.direct": "Audio file attachments and direct audio URLs",
    "premium.sales": "Show upgrade buttons and upsell prompts",
    "feature.saved_playlists": "Saved server playlists",
    "feature.stay_connected": "24/7 mode",
    "feature.feedback": "The /feedback command",
    "feature.anime": "The /anime picture and reaction commands",
}


class FeatureFlags:
    """DB-backed kill switches, polled so changes apply without a restart."""

    def __init__(self, repo: FlagRepo, static_disabled: frozenset[str] = frozenset()) -> None:
        self._repo = repo
        self._static_disabled = static_disabled
        self._values: dict[str, bool] = {}

    async def refresh(self) -> None:
        try:
            self._values = await self._repo.all()
        except Exception:
            log.warning("could not refresh feature flags; keeping previous values", exc_info=True)

    def enabled(self, name: str) -> bool:
        if name in self._static_disabled:
            return False
        return self._values.get(name, True)

    def snapshot(self) -> dict[str, bool]:
        return {name: self.enabled(name) for name in KNOWN_FLAGS}


class RateLimiter:
    """Token bucket per key, with a bounded number of tracked keys."""

    def __init__(self, capacity: int, refill_per_second: float, max_keys: int = 50_000) -> None:
        self.capacity = capacity
        self.refill = refill_per_second
        self.max_keys = max_keys
        self._buckets: OrderedDict[object, tuple[float, float]] = OrderedDict()

    def allow(self, key: object, cost: float = 1.0, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        tokens, last = self._buckets.get(key, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill)
        allowed = tokens >= cost
        if allowed:
            tokens -= cost
        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)
        return allowed

    def retry_after(self, key: object, cost: float = 1.0, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        tokens, last = self._buckets.get(key, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill)
        return 0.0 if tokens >= cost else (cost - tokens) / self.refill


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.commands = Counter("shz_commands_total", "Commands handled", ["command", "outcome"], registry=r)
        self.command_latency = Histogram(
            "shz_command_latency_seconds", "Command handling latency", ["command"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30), registry=r,
        )
        self.guilds = Gauge("shz_guilds", "Guilds the bot is in", registry=r)
        self.sessions = Gauge("shz_voice_sessions", "Active voice sessions", registry=r)
        self.playing = Gauge("shz_playing_sessions", "Sessions currently playing audio", registry=r)
        self.premium_guilds = Gauge("shz_premium_guilds", "Guilds with active premium", registry=r)
        self.tracks_started = Counter("shz_tracks_started_total", "Tracks started", ["provider"], registry=r)
        self.source_errors = Counter("shz_source_errors_total", "Provider errors", ["provider", "kind"], registry=r)
        self.resolve_latency = Histogram(
            "shz_resolve_seconds", "Query/stream resolution latency", ["provider", "kind"],
            buckets=(0.25, 0.5, 1, 2, 4, 8, 15, 30, 60), registry=r,
        )
        self.entitlement_events = Counter(
            "shz_entitlement_events_total", "Entitlement observations", ["source", "changed"], registry=r
        )
        self.reconcile_failures = Counter("shz_entitlement_reconcile_failures_total", "Failed reconciliations", registry=r)
        self.rate_limited = Counter("shz_rate_limited_total", "Interactions rejected by rate limiting", registry=r)
        self.gateway_connected = Gauge("shz_gateway_connected", "1 when connected to the Discord gateway", registry=r)
