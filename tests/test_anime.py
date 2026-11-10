"""/anime: SFW-only categories, URL allowlist, pooling, failures and message text."""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from musicbot.anime import PICTURES, REACTIONS, AnimeClient, AnimeResult, AnimeUnavailable
from musicbot.cogs.fun import Fun, anime_embed, reaction_text
from musicbot.errors import UserError


def page(count=10, host="https://nekos.best/api/v2/hug/"):
    return {"results": [{"url": f"{host}{i}.gif", "anime_name": "Some Show"} for i in range(count)]}


async def test_results_are_pooled_to_spare_the_api():
    fetch = AsyncMock(return_value=page(10))
    client = AnimeClient(fetch)
    urls = {(await client.get("hug")).url for _ in range(10)}
    assert len(urls) == 10 and fetch.await_count == 1
    await client.get("hug")
    assert fetch.await_count == 2
    assert "amount=10" in fetch.await_args.args[0] and fetch.await_args.args[0].startswith("https://nekos.best/api/v2/hug")


async def test_only_nekos_best_https_urls_are_used():
    data = {"results": [{"url": "https://evil.example/x.gif"}, {"url": "http://nekos.best/x.gif"},
                        {"url": "https://nekos.best/api/v2/hug/ok.gif"}]}
    client = AnimeClient(AsyncMock(return_value=data))
    assert (await client.get("hug")).url == "https://nekos.best/api/v2/hug/ok.gif"
    with pytest.raises(AnimeUnavailable):
        await AnimeClient(AsyncMock(return_value={"results": [{"url": "https://evil.example/x.png"}]})).get("neko")


@pytest.mark.parametrize("failure", [aiohttp.ClientError("down"), TimeoutError(), ValueError("bad json")])
async def test_service_failures_become_unavailable(failure):
    with pytest.raises(AnimeUnavailable):
        await AnimeClient(AsyncMock(side_effect=failure)).get("pat")
    with pytest.raises(AnimeUnavailable):
        await AnimeClient(AsyncMock(return_value={"oops": True})).get("pat")


async def test_only_wholesome_categories_can_be_requested():
    not_cute = {"bite", "kick", "punch", "slap", "shoot", "kiss", "peck", "blowkiss", "kabedon", "lappillow",
                "yeet", "bonk", "angry", "tableflip", "baka", "cry"}
    assert not not_cute & (set(REACTIONS) | set(PICTURES))
    assert len(REACTIONS) <= 25 and len(PICTURES) <= 25  # Discord's limit on choices
    with pytest.raises(ValueError):
        await AnimeClient(AsyncMock(return_value=page())).get("slap")


def test_reaction_text_and_embed():
    me, friend = MagicMock(id=1, mention="<@1>"), MagicMock(id=2, mention="<@2>")
    assert reaction_text("hug", me, friend) == "<@1> hugs <@2>"
    assert reaction_text("hug", me, None) == "<@1> wants a hug"
    assert reaction_text("hug", me, me) == "<@1> wants a hug"  # aiming at yourself reads as alone
    embed = anime_embed(AnimeResult(url="https://nekos.best/a.png", artist_name="Someone"), "**Catgirl**")
    assert embed.image.url == "https://nekos.best/a.png" and embed.footer.text == "Art: Someone · via nekos.best"


async def test_kill_switch_and_outage_give_friendly_errors():
    bot = MagicMock()
    cog = Fun(bot)
    bot.flags.enabled.return_value = False
    with pytest.raises(UserError) as exc:
        await cog._get("hug")
    assert exc.value.key == "feature.disabled"
    bot.flags.enabled.return_value = True
    bot.anime.get = AsyncMock(side_effect=AnimeUnavailable("HTTP 503"))
    with pytest.raises(UserError) as exc:
        await cog._get("hug")
    assert exc.value.key == "anime.unavailable"
