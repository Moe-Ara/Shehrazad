"""Cute, safe-for-work anime pictures and reaction GIFs from nekos.best.

nekos.best describes itself as a "fully SFW" anime API with artist and source credits.
On top of that, the bot only ever requests an allowlist of wholesome categories (no
violent or romantic reactions such as bite, kick, punch, slap, shoot or kiss), only
accepts https URLs on nekos.best, and keeps a small pool per category so busy servers
stay far below the API's limit of 200 requests per minute.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

API = "https://nekos.best/api/v2/"
ALLOWED_URL_PREFIX = "https://nekos.best/"
POOL_SIZE = 10
TIMEOUT_SECONDS = 8
USER_AGENT = "Shehrazad Discord bot (+https://github.com/Moe-Ara/Shehrazad)"

# Still pictures: category -> label.
PICTURES: dict[str, str] = {
    "neko": "Catgirl",
    "kitsune": "Foxgirl",
    "waifu": "Waifu",
    "husbando": "Husbando",
}

# Reaction GIFs: category -> (label, text when aimed at someone, text when alone).
REACTIONS: dict[str, tuple[str, str, str]] = {
    "hug": ("Hug", "{a} hugs {b}", "{a} wants a hug"),
    "pat": ("Head pat", "{a} pats {b}", "{a} wants head pats"),
    "cuddle": ("Cuddle", "{a} cuddles {b}", "{a} wants cuddles"),
    "wave": ("Wave", "{a} waves at {b}", "{a} waves hello"),
    "smile": ("Smile", "{a} smiles at {b}", "{a} smiles"),
    "happy": ("Happy", "{a} is happy to see {b}", "{a} is happy"),
    "blush": ("Blush", "{a} blushes at {b}", "{a} blushes"),
    "highfive": ("High five", "{a} high-fives {b}", "{a} wants a high five"),
    "handhold": ("Hold hands", "{a} holds {b}'s hand", "{a} wants to hold hands"),
    "dance": ("Dance", "{a} dances with {b}", "{a} dances"),
    "nom": ("Snack", "{a} shares a snack with {b}", "{a} is snacking"),
    "thumbsup": ("Thumbs up", "{a} gives {b} a thumbs up", "{a} gives a thumbs up"),
    "wink": ("Wink", "{a} winks at {b}", "{a} winks"),
    "nya": ("Nya", "{a} nyas at {b}", "{a} goes nya~"),
    "wag": ("Wag", "{a} wags happily at {b}", "{a} wags happily"),
    "sleep": ("Sleepy", "{a} falls asleep next to {b}", "{a} is sleepy"),
    "clap": ("Clap", "{a} claps for {b}", "{a} claps"),
    "laugh": ("Laugh", "{a} laughs with {b}", "{a} laughs"),
    "feed": ("Feed", "{a} feeds {b}", "{a} is hungry"),
    "tickle": ("Tickle", "{a} tickles {b}", "{a} is ticklish"),
}


class AnimeUnavailable(Exception):
    """The image service failed or returned nothing usable."""


@dataclass(frozen=True)
class AnimeResult:
    url: str
    artist_name: str | None = None
    artist_url: str | None = None
    source_url: str | None = None
    anime_name: str | None = None


FetchJson = Callable[[str], Awaitable[Any]]


def _text(value: object) -> str | None:
    return value.strip()[:100] or None if isinstance(value, str) else None


def _https(value: object) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") else None


class AnimeClient:
    def __init__(self, fetch_json: FetchJson | None = None, *, pool_size: int = POOL_SIZE) -> None:
        self._fetch_json = fetch_json or self._http_get
        self._pool_size = pool_size
        self._pools: dict[str, deque[AnimeResult]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._session: aiohttp.ClientSession | None = None

    async def get(self, category: str) -> AnimeResult:
        if category not in PICTURES and category not in REACTIONS:
            raise ValueError(f"category not allowed: {category}")
        pool = self._pools.setdefault(category, deque())
        async with self._locks.setdefault(category, asyncio.Lock()):
            if not pool:
                pool.extend(await self._load(category))
            return pool.popleft()

    async def _load(self, category: str) -> list[AnimeResult]:
        try:
            data = await self._fetch_json(f"{API}{category}?amount={self._pool_size}")
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            raise AnimeUnavailable(type(exc).__name__) from exc
        items = data.get("results") if isinstance(data, dict) else None
        results = []
        for item in items if isinstance(items, list) else []:
            url = item.get("url") if isinstance(item, dict) else None
            if not isinstance(url, str) or not url.startswith(ALLOWED_URL_PREFIX):
                continue  # never embed anything that isn't hosted by nekos.best
            results.append(AnimeResult(
                url=url,
                artist_name=_text(item.get("artist_name")),
                artist_url=_https(item.get("artist_href")),
                source_url=_https(item.get("source_url")),
                anime_name=_text(item.get("anime_name")),
            ))
        if not results:
            raise AnimeUnavailable("no usable results")
        random.shuffle(results)
        return results

    async def _http_get(self, url: str) -> Any:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS), headers={"User-Agent": USER_AGENT}
            )
        async with self._session.get(url) as response:
            if response.status != 200:
                raise AnimeUnavailable(f"HTTP {response.status}")
            return await response.json(content_type=None)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
