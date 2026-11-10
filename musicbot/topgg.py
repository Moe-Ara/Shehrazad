"""Report the server count to top.gg so the listing and its widgets show real numbers.

Only active when TOPGG_TOKEN is set (the project token from the top.gg dashboard).
Uses `PATCH https://top.gg/api/v1/projects/@me/metrics`. Failures are logged and
never affect the bot; the token is never logged.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import aiohttp

log = logging.getLogger(__name__)

METRICS_URL = "https://top.gg/api/v1/projects/@me/metrics"
TIMEOUT_SECONDS = 15

# (url, headers, json body) -> HTTP status
Send = Callable[[str, dict[str, str], dict[str, int]], Awaitable[int]]


async def _http_patch(url: str, headers: dict[str, str], body: dict[str, int]) -> int:
    async with (
        aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)) as session,
        session.patch(url, headers=headers, json=body) as response,
    ):
        return response.status


class TopggReporter:
    def __init__(self, token: str | None, send: Send | None = None) -> None:
        self._token = token
        self._send = send or _http_patch
        self.last_posted: int | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def post(self, server_count: int) -> bool:
        if not self._token:
            return False
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        try:
            status = await self._send(METRICS_URL, headers, {"server_count": server_count})
        except (TimeoutError, aiohttp.ClientError) as exc:
            log.warning("top.gg stats post failed: %s", type(exc).__name__)
            return False
        if status not in (200, 204):
            log.warning("top.gg stats post rejected with HTTP %s", status)
            return False
        if server_count != self.last_posted:
            log.info("posted server count %d to top.gg", server_count)
        self.last_posted = server_count
        return True
