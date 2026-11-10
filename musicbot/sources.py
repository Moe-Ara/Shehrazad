"""Audio providers: resolve queries into Tracks and Tracks into playable streams.

yt-dlp is blocking, so every extraction runs in a bounded thread pool with a
timeout; the Discord event loop is never blocked. Errors are classified so the
player can tell "this track is gone" (skip it) from "the provider is having a
bad moment" (retry / tell the user to try again later).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any, ClassVar, Protocol
from urllib.parse import parse_qs, unquote, urlparse

from .models import ResolveResult, StreamInfo, Track

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = (".mp3", ".ogg", ".opus", ".wav", ".flac", ".m4a", ".aac", ".webm", ".mp4")
DISCORD_CDN_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
SOUNDCLOUD_HOSTS = ("soundcloud.com",)
SPOTIFY_HOSTS = ("spotify.com", "spotify.link")


# --------------------------------------------------------------------------- errors


class SourceError(Exception):
    """Base error. `key` is an i18n message key; `transient` drives retry policy."""

    key = "source.error"
    transient = False

    def __init__(self, detail: str = "", **params: object) -> None:
        super().__init__(detail or self.key)
        self.detail = detail
        self.params = params


class NoResults(SourceError):
    key = "source.no_results"


class TrackUnavailable(SourceError):
    key = "source.unavailable"


class AgeRestricted(TrackUnavailable):
    key = "source.age_restricted"


class UnsupportedSource(SourceError):
    key = "source.unsupported"


class SpotifyUnsupported(UnsupportedSource):
    key = "source.spotify"


class ProviderUnavailable(SourceError):
    key = "source.provider_down"
    transient = True


class ProviderDisabled(SourceError):
    key = "source.provider_disabled"


_PERMANENT_MARKERS = (
    "video unavailable",
    "private video",
    "this video is not available",
    "has been removed",
    "account associated with this video has been terminated",
    "no longer available",
    "is not available in your country",
    "copyright",
    "unsupported url",
    "does not exist",
    "http error 404",
    "requested format is not available",
    "this live event will begin",
    "video is unavailable",
    "is not available",
    "members-only",
)
_AGE_MARKERS = ("confirm your age", "age-restricted", "inappropriate for some users")
_TRANSIENT_MARKERS = (
    "http error 429",
    "too many requests",
    "timed out",
    "timeout",
    "temporary failure",
    "connection reset",
    "connection refused",
    "remote end closed",
    "http error 5",
    "unable to download",
    "not a bot",
    "network is unreachable",
    "name or service not known",
)


def classify_extraction_error(exc: BaseException) -> SourceError:
    if isinstance(exc, SourceError):
        return exc
    text = str(exc).lower()
    if any(marker in text for marker in _AGE_MARKERS):
        return AgeRestricted(str(exc))
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return ProviderUnavailable(str(exc))
    if any(marker in text for marker in _PERMANENT_MARKERS):
        return TrackUnavailable(str(exc))
    # Unknown failures are treated as transient: retrying is safer than
    # telling the user a perfectly fine track is gone.
    return ProviderUnavailable(str(exc))


# --------------------------------------------------------------------------- URL helpers


def is_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith(("http://", "https://", "www."))


def _parse(url: str):
    url = url.strip()
    return urlparse(url if "://" in url else f"https://{url}")


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    host = host.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in domains)


def is_playlist_url(url: str) -> bool:
    """True when the URL points at a collection of tracks, not a single one.

    A link to one specific song is always a single song, even when the site
    tacks playlist context onto it (YouTube `&list=` mixes, SoundCloud `?in=`).
    """
    if not is_url(url):
        return False
    parsed = _parse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower().rstrip("/")
    params = parse_qs(parsed.query)
    if _host_matches(host, ("youtu.be",)):
        return False
    if _host_matches(host, YOUTUBE_HOSTS):
        if params.get("v") or path.startswith(("/shorts/", "/live/", "/embed/")):
            return False
        if path == "/playlist" and params.get("list"):
            return True
        if host.startswith("music.") and path.startswith("/browse/"):
            return True
        return bool(params.get("list"))
    if _host_matches(host, SOUNDCLOUD_HOSTS):
        return "/sets/" in path
    if _host_matches(host, SPOTIFY_HOSTS):
        return "/playlist/" in path or "/album/" in path
    return path.endswith("/playlist")


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def pick_best_entry(entries: list[dict[str, Any]], query: str) -> dict[str, Any]:
    best, best_score = entries[0], -1.0
    for entry in entries:
        title = entry.get("title") or ""
        artist = entry.get("artist") or entry.get("uploader") or entry.get("channel") or ""
        combined = " ".join(p for p in (title, artist) if p)
        score = similarity(query, combined or title)
        if score > best_score:
            best, best_score = entry, score
    return best


def _is_public_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


# --------------------------------------------------------------------------- providers


class AudioProvider(Protocol):
    name: str
    show_artwork: bool

    def handles(self, url: str) -> bool: ...

    async def resolve(self, query: str, *, max_tracks: int) -> ResolveResult: ...

    async def stream(self, track: Track) -> StreamInfo: ...


class _TTLCache:
    def __init__(self, size: int = 256, ttl: float = 1800.0) -> None:
        self._data: OrderedDict[str, tuple[float, StreamInfo]] = OrderedDict()
        self._size, self._ttl = size, ttl

    def get(self, key: str) -> StreamInfo | None:
        item = self._data.get(key)
        if item is None:
            return None
        if time.monotonic() - item[0] > self._ttl:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return item[1]

    def put(self, key: str, value: StreamInfo) -> None:
        self._data[key] = (time.monotonic(), value)
        self._data.move_to_end(key)
        while len(self._data) > self._size:
            self._data.popitem(last=False)


class _YtdlLogger:
    """Route yt-dlp's console output into our logs (errors surface as exceptions anyway)."""

    def debug(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp warning: %s", msg)

    def error(self, msg: str) -> None:
        log.debug("yt-dlp error: %s", msg)


class YouTubeProvider:
    """YouTube (and SoundCloud) through yt-dlp."""

    name = "youtube"
    show_artwork = True

    BASE_OPTIONS: ClassVar[dict[str, Any]] = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "socket_timeout": 15,
        # Never fall back to the generic extractor: it would fetch arbitrary URLs.
        "allowed_extractors": [r"youtube(:.*)?", r"soundcloud(:.*)?"],
        "extractor_args": {"youtube": {"player_client": ["ios", "android"]}},
        "logger": _YtdlLogger(),
    }

    def __init__(
        self,
        executor: ThreadPoolExecutor,
        *,
        timeout: float = 30.0,
        ytdl_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._executor = executor
        self._timeout = timeout
        self._ytdl_factory = ytdl_factory or self._default_factory
        self._streams = _TTLCache()

    @staticmethod
    def _default_factory(options: dict[str, Any]) -> Any:
        import yt_dlp

        return yt_dlp.YoutubeDL(options)

    def handles(self, url: str) -> bool:
        host = _parse(url).netloc
        return _host_matches(host, YOUTUBE_HOSTS) or _host_matches(host, SOUNDCLOUD_HOSTS)

    async def _extract(self, target: str, options: dict[str, Any], limit_seconds: float | None = None) -> Any:
        def run() -> Any:
            with self._ytdl_factory(options) as ydl:
                return ydl.extract_info(target, download=False)

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(self._executor, run), limit_seconds or self._timeout
            )
        except TimeoutError as exc:
            raise ProviderUnavailable("extraction timed out") from exc
        except SourceError:
            raise
        except Exception as exc:  # yt-dlp raises DownloadError/ExtractorError/...
            raise classify_extraction_error(exc) from exc

    def _track_from_info(self, info: dict[str, Any], fallback_url: str) -> Track:
        url = info.get("webpage_url") or info.get("original_url") or info.get("url") or fallback_url
        if url and not str(url).startswith("http"):
            url = f"https://www.youtube.com/watch?v={url}"
        thumbs = info.get("thumbnails") or []
        artwork = info.get("thumbnail") or (thumbs[-1].get("url") if thumbs else None)
        return Track(
            title=str(info.get("title") or info.get("track") or "Unknown track"),
            url=str(url),
            provider=self.name,
            duration=float(info["duration"]) if info.get("duration") else None,
            artist=info.get("artist") or info.get("uploader") or info.get("channel"),
            artwork_url=artwork,
        )

    async def resolve(self, query: str, *, max_tracks: int) -> ResolveResult:
        if not is_url(query):
            info = await self._extract(
                f"ytsearch5:{query}", {**self.BASE_OPTIONS, "extract_flat": "in_playlist"}
            )
            entries = [e for e in (info or {}).get("entries") or [] if e]
            if not entries:
                raise NoResults(query=query)
            best = pick_best_entry(entries, query)
            return ResolveResult([self._track_from_info(best, query)])

        if is_playlist_url(query):
            options = {
                **self.BASE_OPTIONS,
                "noplaylist": False,
                "extract_flat": "in_playlist",
                "playlistend": max_tracks + 1,
            }
            info = await self._extract(query, options, limit_seconds=self._timeout * 2)
            entries = [e for e in (info or {}).get("entries") or [] if e]
            if not entries:
                if info and info.get("url"):
                    return ResolveResult([self._track_from_info(info, query)])
                raise NoResults(query=query)
            tracks = [self._track_from_info(e, query) for e in entries[:max_tracks]]
            return ResolveResult(
                tracks,
                playlist_title=str(info.get("title") or "Playlist"),
                truncated=len(entries) > max_tracks,
            )

        info = await self._extract(query, self.BASE_OPTIONS)
        if not info:
            raise NoResults(query=query)
        if info.get("entries"):
            entries = [e for e in info["entries"] if e]
            if not entries:
                raise NoResults(query=query)
            info = entries[0]
        track = self._track_from_info(info, query)
        if info.get("url"):
            self._streams.put(track.url, self._stream_from_info(info))
        return ResolveResult([track])

    @staticmethod
    def _stream_from_info(info: dict[str, Any]) -> StreamInfo:
        headers = dict(info.get("http_headers") or {})
        headers.setdefault("Referer", "https://www.youtube.com/")
        headers.setdefault("Origin", "https://www.youtube.com")
        return StreamInfo(
            url=str(info["url"]),
            headers={str(k): str(v) for k, v in headers.items()},
            duration=float(info["duration"]) if info.get("duration") else None,
            is_live=bool(info.get("is_live")),
        )

    async def stream(self, track: Track) -> StreamInfo:
        cached = self._streams.get(track.url)
        if cached is not None:
            return cached
        info = await self._extract(track.url, self.BASE_OPTIONS)
        if isinstance(info, dict) and info.get("entries"):
            info = next((e for e in info["entries"] if e), None)
        if not isinstance(info, dict) or not info.get("url"):
            raise TrackUnavailable("no playable stream")
        stream = self._stream_from_info(info)
        self._streams.put(track.url, stream)
        return stream

    def invalidate(self, track: Track) -> None:
        self._streams._data.pop(track.url, None)


class DirectProvider:
    """Audio files: Discord attachments, and (optionally) public audio URLs."""

    name = "direct"
    show_artwork = False

    def __init__(self, executor: ThreadPoolExecutor, *, allow_any_url: bool = False) -> None:
        self._executor = executor
        self._allow_any_url = allow_any_url

    def handles(self, url: str) -> bool:
        parsed = _parse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        is_audio = PurePosixPath(parsed.path.lower()).suffix in AUDIO_EXTENSIONS
        if _host_matches(parsed.netloc, DISCORD_CDN_HOSTS):
            return is_audio
        return self._allow_any_url and is_audio

    async def _check_public_host(self, host: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.run_in_executor(
                self._executor, lambda: socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            )
        except OSError as exc:
            raise TrackUnavailable("host does not resolve") from exc
        if not infos or not all(_is_public_ip(str(info[4][0])) for info in infos):
            raise UnsupportedSource("private address")

    async def resolve(self, query: str, *, max_tracks: int) -> ResolveResult:
        parsed = _parse(query)
        if parsed.scheme != "https" and not _host_matches(parsed.netloc, DISCORD_CDN_HOSTS):
            raise UnsupportedSource("https required")
        if not _host_matches(parsed.netloc, DISCORD_CDN_HOSTS):
            await self._check_public_host(parsed.hostname or "")
        name = unquote(PurePosixPath(parsed.path).name) or "Audio file"
        return ResolveResult([Track(title=name, url=query.strip(), provider=self.name)])

    async def stream(self, track: Track) -> StreamInfo:
        return StreamInfo(url=track.url)


# --------------------------------------------------------------------------- registry


class ProviderRegistry:
    def __init__(
        self,
        providers: list[AudioProvider],
        *,
        is_enabled: Callable[[str], bool] = lambda name: True,
        search_provider: str = "youtube",
    ) -> None:
        self._providers = {p.name: p for p in providers}
        self._is_enabled = is_enabled
        self._search_provider = search_provider

    def get(self, name: str) -> AudioProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise UnsupportedSource(f"unknown provider {name}")
        if not self._is_enabled(name):
            raise ProviderDisabled(provider=name)
        return provider

    def provider_for(self, query: str) -> AudioProvider:
        if is_url(query):
            host = _parse(query).netloc
            if _host_matches(host, SPOTIFY_HOSTS):
                raise SpotifyUnsupported()
            for provider in self._providers.values():
                if provider.handles(query):
                    if not self._is_enabled(provider.name):
                        raise ProviderDisabled(provider=provider.name)
                    return provider
            raise UnsupportedSource(query)
        return self.get(self._search_provider)

    async def resolve(self, query: str, *, max_tracks: int) -> ResolveResult:
        query = query.strip()
        if not query:
            raise NoResults()
        if len(query) > 500:
            raise UnsupportedSource("query too long")
        provider = self.provider_for(query)
        return await provider.resolve(query, max_tracks=max_tracks)

    async def stream(self, track: Track) -> StreamInfo:
        return await self.get(track.provider).stream(track)

    def show_artwork(self, track: Track) -> bool:
        provider = self._providers.get(track.provider)
        return bool(provider and provider.show_artwork)

    def invalidate(self, track: Track) -> None:
        provider = self._providers.get(track.provider)
        invalidate = getattr(provider, "invalidate", None)
        if callable(invalidate):
            invalidate(track)
