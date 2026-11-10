"""Structured logging with correlation IDs, guild context and secret redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
guild_context: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "guild_id", default=None
)

# Discord bot tokens: base64(user id).timestamp.hmac
_TOKEN_RE = re.compile(r"[A-Za-z\d_-]{23,28}\.[A-Za-z\d_-]{6,7}\.[A-Za-z\d_-]{27,40}")
_SECRET_PARAM_RE = re.compile(
    r"((?:token|secret|password|sig|lsig|signature|key)=)[^&\s\"']+", re.IGNORECASE
)
# Signed media URLs carry IPs and signatures in the query string.
_MEDIA_URL_RE = re.compile(r"(https?://[^\s?\"']*googlevideo\.com/[^\s?\"']*)\?[^\s\"']+")


def redact(text: str) -> str:
    text = _MEDIA_URL_RE.sub(r"\1?<redacted>", text)
    text = _TOKEN_RE.sub("<redacted-token>", text)
    return _SECRET_PARAM_RE.sub(r"\1<redacted>", text)


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def bind(*, correlation: str | None = None, guild_id: int | None = None) -> Iterator[str]:
    """Bind a correlation ID (and optionally a guild) to the current context."""
    cid = correlation or new_correlation_id()
    token_c = correlation_id.set(cid)
    token_g = guild_context.set(guild_id) if guild_id is not None else None
    try:
        yield cid
    finally:
        correlation_id.reset(token_c)
        if token_g is not None:
            guild_context.reset(token_g)


_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.__dict__["correlation_id"] = correlation_id.get()
        if not hasattr(record, "guild_id"):
            record.__dict__["guild_id"] = guild_context.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key in ("correlation_id", "guild_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key in payload or key.startswith("_"):
                continue
            if key in ("correlation_id", "guild_id"):
                continue
            payload[key] = value if isinstance(value, (int, float, bool)) or value is None else redact(str(value))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = redact(super().format(record))
        ctx = []
        cid = getattr(record, "correlation_id", None)
        gid = getattr(record, "guild_id", None)
        if cid:
            ctx.append(f"cid={cid}")
        if gid:
            ctx.append(f"guild={gid}")
        return f"{base} [{' '.join(ctx)}]" if ctx else base


def setup_logging(level: str = "INFO", json_output: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(JsonFormatter() if json_output else TextFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # Library noise
    logging.getLogger("discord.gateway").setLevel(max(logging.INFO, root.level))
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
