"""Process entry point: config validation, logging, graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

import discord

from .config import ConfigError, Settings, load_dotenv_file, load_settings
from .logging_setup import setup_logging

log = logging.getLogger("musicbot")


async def _serve(config: Settings) -> int:
    from .bot import ShehrazadBot

    bot = ShehrazadBot(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows: Ctrl+C raises KeyboardInterrupt instead
    async with bot:
        runner = asyncio.create_task(bot.start(config.token), name="discord")
        stopper = asyncio.create_task(stop.wait(), name="signals")
        done, _ = await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if stopper in done:
            log.info("signal received; shutting down gracefully")
            await bot.close()
        try:
            await runner
        except discord.LoginFailure:
            log.error("Discord rejected the bot token. Check DISCORD_TOKEN.")
            return 1
        except discord.PrivilegedIntentsRequired:
            log.error("Discord refused a privileged intent. The bot needs none; check the Developer Portal.")
            return 1
        finally:
            stopper.cancel()
    return 0


def main() -> None:
    load_dotenv_file()
    try:
        config = load_settings()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    setup_logging(config.log_level, config.log_json)
    log.info("starting (environment=%s)", config.environment)
    if os.environ.get("LEGACY_PREFIX_COMMANDS", "").strip().lower() in ("1", "true", "yes", "on"):
        log.warning("LEGACY_PREFIX_COMMANDS is no longer supported: the bot answers slash commands only")
    try:
        code = asyncio.run(_serve(config))
    except KeyboardInterrupt:
        code = 0
    raise SystemExit(code)
