"""HTTP health and metrics endpoints (aiohttp, already a discord.py dependency).

/healthz  liveness: the process is up and the gateway hasn't been down too long
/readyz   readiness: connected to Discord and the database answers
/metrics  Prometheus exposition

Bind this to localhost or a private network; it is not meant for the internet.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

if TYPE_CHECKING:
    from .bot import ShehrazadBot

log = logging.getLogger(__name__)

STARTUP_GRACE_SECONDS = 180
GATEWAY_DOWN_LIMIT_SECONDS = 300


class HealthServer:
    def __init__(self, bot: ShehrazadBot, host: str, port: int) -> None:
        self.bot = bot
        self.host, self.port = host, port
        self._runner: web.AppRunner | None = None
        self.started_at = time.monotonic()

    def _alive(self) -> tuple[bool, str]:
        bot = self.bot
        if bot.is_ready() and bot.gateway_down_since is None:
            return True, "ok"
        if time.monotonic() - self.started_at < STARTUP_GRACE_SECONDS:
            return True, "starting"
        if bot.gateway_down_since is not None and time.monotonic() - bot.gateway_down_since < GATEWAY_DOWN_LIMIT_SECONDS:
            return True, "reconnecting"
        return False, "gateway down"

    async def healthz(self, request: web.Request) -> web.Response:
        ok, status = self._alive()
        return web.json_response({"status": status}, status=200 if ok else 503)

    async def readyz(self, request: web.Request) -> web.Response:
        db_ok = await self.bot.db.ping()
        ready = self.bot.is_ready() and db_ok
        body = {
            "ready": ready,
            "gateway": self.bot.is_ready(),
            "database": db_ok,
            "guilds": len(self.bot.guilds),
            "sessions": self.bot.manager.active_count if hasattr(self.bot, "manager") else 0,
            "latency_ms": round(self.bot.latency * 1000) if self.bot.is_ready() else None,
            "entitlements_last_reconcile": str(self.bot.premium.last_reconcile_ok) if self.bot.premium.last_reconcile_ok else None,
        }
        return web.json_response(body, status=200 if ready else 503)

    async def metrics(self, request: web.Request) -> web.Response:
        payload = generate_latest(self.bot.metrics.registry)
        return web.Response(body=payload, headers={"Content-Type": CONTENT_TYPE_LATEST})

    async def start(self) -> None:
        if self.port <= 0:
            return
        app = web.Application()
        app.router.add_get("/healthz", self.healthz)
        app.router.add_get("/readyz", self.readyz)
        app.router.add_get("/metrics", self.metrics)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        log.info("health server listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
