"""top.gg server-count reporting."""

import logging
from unittest.mock import AsyncMock

import aiohttp

from musicbot.config import load_settings
from musicbot.topgg import METRICS_URL, TopggReporter


async def test_disabled_without_token():
    send = AsyncMock()
    reporter = TopggReporter(None, send)
    assert not reporter.enabled
    assert await reporter.post(3) is False
    send.assert_not_awaited()


async def test_posts_server_count_with_bearer_token():
    send = AsyncMock(return_value=204)
    reporter = TopggReporter("secret-topgg-token", send)
    assert await reporter.post(3) is True
    url, headers, body = send.await_args.args
    assert url == METRICS_URL == "https://top.gg/api/v1/projects/@me/metrics"
    assert headers["Authorization"] == "Bearer secret-topgg-token" and body == {"server_count": 3}
    assert reporter.last_posted == 3


async def test_failures_are_logged_without_the_token(caplog):
    caplog.set_level(logging.WARNING)
    for send in (AsyncMock(return_value=401), AsyncMock(side_effect=aiohttp.ClientError("down")), AsyncMock(side_effect=TimeoutError())):
        assert await TopggReporter("secret-topgg-token", send).post(3) is False
    assert caplog.records and "secret-topgg-token" not in caplog.text


def test_token_comes_from_env_and_is_hidden(tmp_path):
    settings = load_settings({"DISCORD_TOKEN": "x", "TOPGG_TOKEN": "secret-topgg-token"}, properties_path=tmp_path / "none")
    assert settings.topgg_token == "secret-topgg-token" and "secret-topgg-token" not in repr(settings)
