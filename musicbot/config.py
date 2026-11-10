"""Environment-driven configuration with validation.

Settings come from environment variables (optionally a `.env` file). For
backwards compatibility the bot token can still come from the legacy
`properties.properties` file (`[configs] BOT_TOKEN`).

The token is never included in repr() or error messages.
"""

from __future__ import annotations

import configparser
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

KNOWN_PROVIDERS = frozenset({"youtube", "direct"})
ENVIRONMENTS = ("production", "staging", "development")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(Exception):
    """Raised when configuration is missing or invalid. Never contains secrets."""


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    environment: str = "development"
    database_path: Path = Path("data/shehrazad.db")
    log_level: str = "INFO"
    log_json: bool = False
    health_host: str = "0.0.0.0"
    health_port: int = 8080
    application_id: int | None = None
    premium_sku_ids: frozenset[int] = frozenset()
    support_url: str | None = None
    website_url: str | None = None
    terms_url: str | None = None
    privacy_url: str | None = None
    feedback_channel_id: int | None = None
    enabled_providers: frozenset[str] = frozenset({"youtube", "direct"})
    allow_direct_urls: bool = False
    legacy_prefix_commands: bool = False
    legacy_prefix: str = "!"
    dev_guild_id: int | None = None
    sync_commands: bool = True
    max_sessions_soft: int = 50
    max_sessions_hard: int = 75
    history_retention_days: int = 30
    departed_guild_retention_days: int = 30
    feedback_retention_days: int = 90
    show_artwork: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def premium_configured(self) -> bool:
        return bool(self.premium_sku_ids)


def _parse_bool(name: str, raw: str, errors: list[str]) -> bool:
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    errors.append(f"{name} must be true/false, got {raw!r}")
    return False


def _parse_int(name: str, raw: str, errors: list[str], *, minimum: int | None = None) -> int | None:
    try:
        value = int(raw.strip())
    except ValueError:
        errors.append(f"{name} must be an integer, got {raw!r}")
        return None
    if minimum is not None and value < minimum:
        errors.append(f"{name} must be >= {minimum}, got {value}")
        return None
    return value


def _parse_id_set(name: str, raw: str, errors: list[str]) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            errors.append(f"{name} must be a comma-separated list of numeric IDs, got {part!r}")
            continue
        ids.add(int(part))
    return frozenset(ids)


def _parse_url(name: str, raw: str, errors: list[str]) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if not value.startswith(("https://", "http://")):
        errors.append(f"{name} must start with https://")
        return None
    return value


def _legacy_token(properties_path: Path) -> str | None:
    if not properties_path.is_file():
        return None
    parser = configparser.RawConfigParser()
    try:
        parser.read(properties_path, encoding="utf-8")
        token = parser.get("configs", "BOT_TOKEN", fallback="").strip()
    except configparser.Error:
        return None
    return token or None


def load_dotenv_file(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from a .env file without overriding real env vars."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_settings(
    env: Mapping[str, str] | None = None,
    properties_path: Path = Path("properties.properties"),
    *,
    require_token: bool = True,
) -> Settings:
    env = os.environ if env is None else env
    errors: list[str] = []

    def get(name: str) -> str | None:
        value = env.get(name)
        return value if value is not None and value.strip() != "" else None

    token = get("DISCORD_TOKEN") or _legacy_token(properties_path)
    if require_token and not token:
        errors.append(
            "DISCORD_TOKEN is not set (and no BOT_TOKEN found in properties.properties)"
        )

    kwargs: dict[str, object] = {}

    if (raw := get("ENVIRONMENT")) is not None:
        value = raw.strip().lower()
        if value not in ENVIRONMENTS:
            errors.append(f"ENVIRONMENT must be one of {', '.join(ENVIRONMENTS)}")
        kwargs["environment"] = value
    if (raw := get("DATABASE_PATH")) is not None:
        kwargs["database_path"] = Path(raw.strip())
    if (raw := get("LOG_LEVEL")) is not None:
        value = raw.strip().upper()
        if value not in LOG_LEVELS:
            errors.append(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}")
        kwargs["log_level"] = value
    for name, attr in (
        ("LOG_JSON", "log_json"),
        ("ALLOW_DIRECT_URLS", "allow_direct_urls"),
        ("LEGACY_PREFIX_COMMANDS", "legacy_prefix_commands"),
        ("SYNC_COMMANDS", "sync_commands"),
        ("SHOW_ARTWORK", "show_artwork"),
    ):
        if (raw := get(name)) is not None:
            kwargs[attr] = _parse_bool(name, raw, errors)
    if (raw := get("HEALTH_HOST")) is not None:
        kwargs["health_host"] = raw.strip()
    for name, attr, minimum in (
        ("HEALTH_PORT", "health_port", 0),
        ("MAX_SESSIONS_SOFT", "max_sessions_soft", 1),
        ("MAX_SESSIONS_HARD", "max_sessions_hard", 1),
        ("HISTORY_RETENTION_DAYS", "history_retention_days", 1),
        ("DEPARTED_GUILD_RETENTION_DAYS", "departed_guild_retention_days", 0),
        ("FEEDBACK_RETENTION_DAYS", "feedback_retention_days", 1),
    ):
        if (raw := get(name)) is not None:
            parsed = _parse_int(name, raw, errors, minimum=minimum)
            if parsed is not None:
                kwargs[attr] = parsed
    for name, attr in (
        ("APPLICATION_ID", "application_id"),
        ("FEEDBACK_CHANNEL_ID", "feedback_channel_id"),
        ("DEV_GUILD_ID", "dev_guild_id"),
    ):
        if (raw := get(name)) is not None:
            kwargs[attr] = _parse_int(name, raw, errors, minimum=1)
    if (raw := get("PREMIUM_SKU_IDS")) is not None:
        kwargs["premium_sku_ids"] = _parse_id_set("PREMIUM_SKU_IDS", raw, errors)
    for name, attr in (
        ("SUPPORT_URL", "support_url"),
        ("WEBSITE_URL", "website_url"),
        ("TERMS_URL", "terms_url"),
        ("PRIVACY_URL", "privacy_url"),
    ):
        if (raw := get(name)) is not None:
            kwargs[attr] = _parse_url(name, raw, errors)
    if (raw := get("ENABLED_PROVIDERS")) is not None:
        providers = frozenset(p.strip().lower() for p in raw.split(",") if p.strip())
        unknown = providers - KNOWN_PROVIDERS
        if unknown:
            errors.append(
                f"ENABLED_PROVIDERS has unknown providers: {', '.join(sorted(unknown))}"
            )
        kwargs["enabled_providers"] = providers & KNOWN_PROVIDERS
    if (raw := get("LEGACY_PREFIX")) is not None:
        prefix = raw.strip()
        if not prefix or len(prefix) > 5:
            errors.append("LEGACY_PREFIX must be 1-5 characters")
        kwargs["legacy_prefix"] = prefix

    soft = kwargs.get("max_sessions_soft", Settings.max_sessions_soft)
    hard = kwargs.get("max_sessions_hard", Settings.max_sessions_hard)
    if isinstance(soft, int) and isinstance(hard, int) and hard < soft:
        errors.append("MAX_SESSIONS_HARD must be >= MAX_SESSIONS_SOFT")

    if errors:
        raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(errors))

    return Settings(token=token or "", **kwargs)  # type: ignore[arg-type]
