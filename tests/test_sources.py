import asyncio
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest

from musicbot.models import Track
from musicbot.sources import (
    AgeRestricted,
    DirectProvider,
    NoResults,
    ProviderDisabled,
    ProviderRegistry,
    ProviderUnavailable,
    SpotifyUnsupported,
    TrackUnavailable,
    UnsupportedSource,
    YouTubeProvider,
    classify_extraction_error,
    is_playlist_url,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", False),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1", False),
        ("https://youtu.be/dQw4w9WgXcQ?list=PLxyz", False),
        ("https://www.youtube.com/shorts/abc", False),
        ("https://music.youtube.com/watch?v=abc&list=RDAMVMabc", False),
        ("www.youtube.com/watch?v=abc&list=RDabc", False),
        ("https://www.youtube.com/playlist?list=PLxyz", True),
        ("https://music.youtube.com/browse/MPREb_abc", True),
        ("https://www.youtube.com/watch?list=PLxyz", True),
        ("https://soundcloud.com/a/track?in=a/sets/album", False),
        ("https://soundcloud.com/a/sets/album", True),
        ("https://open.spotify.com/playlist/1", True),
        ("never gonna give you up", False),
        ("https://evil-youtube.com.attacker.net/playlist?list=1", True),  # unknown host: generic rule
    ],
)
def test_is_playlist_url(url, expected):
    assert is_playlist_url(url) is expected


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("ERROR: [youtube] x: Video unavailable", TrackUnavailable),
        ("ERROR: [youtube] xxxxxxxxxxx: This video is unavailable", TrackUnavailable),  # real yt-dlp text
        ("ERROR: Private video. Sign in", TrackUnavailable),
        ("Sign in to confirm your age", AgeRestricted),
        ("HTTP Error 429: Too Many Requests", ProviderUnavailable),
        ("Sign in to confirm you're not a bot", ProviderUnavailable),
        ("something completely unexpected", ProviderUnavailable),
    ],
)
def test_error_classification(message, kind):
    err = classify_extraction_error(Exception(message))
    assert isinstance(err, kind)
    assert err.transient is (kind is ProviderUnavailable)


class FakeYDL:
    def __init__(self, results, options):
        self.results, self.options = results, options

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, target, download=False):
        value = self.results(target, self.options)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def executor():
    pool = ThreadPoolExecutor(max_workers=2)
    yield pool
    pool.shutdown(wait=False)


def yt(executor, results, timeout=5.0):
    return YouTubeProvider(executor, timeout=timeout, ytdl_factory=lambda opts: FakeYDL(results, opts))


async def test_search_picks_best_match_and_uses_flat_extraction(executor):
    seen = {}

    def results(target, options):
        seen["target"], seen["flat"] = target, options.get("extract_flat")
        return {"entries": [
            {"title": "Fairuz - Kifak Inta (Live)", "url": "https://www.youtube.com/watch?v=a", "duration": 300},
            {"title": "Kifak Inta", "uploader": "Fairuz", "url": "https://www.youtube.com/watch?v=b", "duration": 212},
        ]}

    result = await yt(executor, results).resolve("kifak inta fairuz", max_tracks=10)
    assert seen == {"target": "ytsearch5:kifak inta fairuz", "flat": "in_playlist"}
    assert result.tracks[0].url.endswith("v=b")
    assert not result.is_playlist


async def test_single_song_link_with_mix_is_one_track(executor):
    def results(target, options):
        assert options["noplaylist"] is True
        return {"title": "Song", "webpage_url": "https://www.youtube.com/watch?v=x", "url": "https://media/stream", "duration": 10}

    provider = yt(executor, results)
    result = await provider.resolve("https://www.youtube.com/watch?v=x&list=RDx", max_tracks=100)
    assert len(result.tracks) == 1 and not result.is_playlist
    stream = await provider.stream(result.tracks[0])  # cached from resolve
    assert stream.url == "https://media/stream"


async def test_playlist_is_truncated_to_tier_limit(executor):
    def results(target, options):
        assert options["playlistend"] == 4
        return {"title": "Mix", "entries": [{"title": f"t{i}", "url": f"https://www.youtube.com/watch?v={i}"} for i in range(4)]}

    result = await yt(executor, results).resolve("https://www.youtube.com/playlist?list=PL1", max_tracks=3)
    assert result.playlist_title == "Mix"
    assert len(result.tracks) == 3 and result.truncated


async def test_empty_search_raises_no_results(executor):
    with pytest.raises(NoResults):
        await yt(executor, lambda t, o: {"entries": []}).resolve("zzz", max_tracks=5)


async def test_extraction_timeout_is_transient(executor):
    def slow(target, options):
        import time

        time.sleep(0.5)
        return {}

    with pytest.raises(ProviderUnavailable):
        await yt(executor, slow, timeout=0.05).resolve("anything", max_tracks=5)


async def test_extraction_does_not_block_event_loop(executor):
    import time

    def slow(target, options):
        time.sleep(0.3)
        return {"entries": [{"title": "x", "url": "https://www.youtube.com/watch?v=x"}]}

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    await yt(executor, slow).resolve("x", max_tracks=1)
    task.cancel()
    assert ticks >= 10


async def test_registry_routing_and_kill_switch(executor):
    enabled = {"youtube": True, "direct": True}
    registry = ProviderRegistry(
        [yt(executor, lambda t, o: {"entries": [{"title": "a", "url": "https://www.youtube.com/watch?v=a"}]}), DirectProvider(executor)],
        is_enabled=lambda name: enabled[name],
    )
    with pytest.raises(SpotifyUnsupported):
        await registry.resolve("https://open.spotify.com/track/1", max_tracks=1)
    with pytest.raises(UnsupportedSource):
        await registry.resolve("https://example.com/page", max_tracks=1)
    with pytest.raises(UnsupportedSource):
        await registry.resolve("x" * 600, max_tracks=1)
    assert (await registry.resolve("hello", max_tracks=1)).tracks
    enabled["youtube"] = False
    with pytest.raises(ProviderDisabled):
        await registry.resolve("hello", max_tracks=1)


def test_direct_provider_only_discord_attachments_by_default(executor):
    strict = DirectProvider(executor)
    assert strict.handles("https://cdn.discordapp.com/attachments/1/2/song.mp3")
    assert not strict.handles("https://cdn.discordapp.com/attachments/1/2/image.png")
    assert not strict.handles("https://example.com/song.mp3")
    assert DirectProvider(executor, allow_any_url=True).handles("https://example.com/song.mp3")


async def test_direct_provider_blocks_private_addresses(executor, monkeypatch):
    provider = DirectProvider(executor, allow_any_url=True)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(UnsupportedSource):
        await provider.resolve("https://intranet.example/song.mp3", max_tracks=1)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    result = await provider.resolve("https://example.com/My%20Song.mp3", max_tracks=1)
    assert result.tracks[0].title == "My Song.mp3"
    with pytest.raises(UnsupportedSource):
        await provider.resolve("http://example.com/song.mp3", max_tracks=1)


def test_youtube_provider_never_uses_generic_extractor():
    assert all("generic" not in pattern for pattern in YouTubeProvider.BASE_OPTIONS["allowed_extractors"])
    assert "nocheckcertificate" not in YouTubeProvider.BASE_OPTIONS


def test_track_roundtrip():
    track = Track("t", "https://x", "youtube", 3.0, "a", None, 5)
    assert Track.from_dict(track.to_dict()) == track
