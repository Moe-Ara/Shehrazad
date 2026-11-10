import logging
import re
from pathlib import Path

import pytest

from musicbot.config import ConfigError, load_settings
from musicbot.i18n import CATALOGS, EN, resolve_locale, t
from musicbot.logging_setup import JsonFormatter, bind, redact
from musicbot.ops import RateLimiter

ROOT = Path(__file__).resolve().parents[1] / "musicbot"
# Assembled at runtime so secret scanners don't flag this obviously fake token.
FAKE_TOKEN = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4", "GabcdE", "abcdefghijklmnopqrstuvwxyz0123456789AB"])


# ------------------------------------------------------------------ i18n


def _literal_keys() -> tuple[set[str], set[str]]:
    """All dotted string literals in the code, and the ones that are NOT message keys."""
    literal = re.compile(r"\"([a-z_]+\.[a-z0-9_]+)\"")
    metric_names = re.compile(r"run\(\s*self\.bot,\s*interaction,\s*\"([^\"]+)\"")
    audit_actions = re.compile(r"\.record\([^,]+,\s*\"([^\"]+)\"")
    keys: set[str] = set()
    excluded: set[str] = set()
    for path in ROOT.rglob("*.py"):
        if path.name == "i18n.py":
            continue
        text = path.read_text(encoding="utf-8")
        keys |= set(literal.findall(text))
        excluded |= set(metric_names.findall(text)) | set(audit_actions.findall(text))
    from musicbot.ops import KNOWN_FLAGS

    excluded |= set(KNOWN_FLAGS)
    excluded |= {"musicbot.migrations", "discord.gateway", "discord.http"}  # module / logger names
    return keys, excluded


def test_every_referenced_key_exists_in_english():
    keys, excluded = _literal_keys()
    catalog_prefixes = {k.split(".")[0] for k in EN}
    candidates = {k for k in keys if k.split(".")[0] in catalog_prefixes}
    missing = sorted(k for k in candidates if k not in EN and k not in excluded)
    assert missing == []


def test_dynamic_key_families_exist():
    for mode in ("off", "track", "queue"):
        assert f"loop.{mode}" in EN
    for state in ("idle", "loading", "playing", "paused", "ended"):
        assert f"status.player_{state}" in EN
    from musicbot.cogs.info import HELP_CATEGORIES
    from musicbot.premium import Feature

    for key, (label, _emoji, names) in HELP_CATEGORIES.items():
        assert label in EN and f"help.blurb_{key}" in EN
        for name in names:
            assert "cmd." + name.replace(" ", "_").replace("-", "_") in EN
    for feature in Feature:
        assert f"feature.{feature.value}" in EN


def test_english_only():
    assert list(CATALOGS) == ["en"]
    arabic = re.compile("[\\u0600-\\u06ff]")  # Arabic block
    for path in ROOT.rglob("*.py"):
        assert not arabic.search(path.read_text(encoding="utf-8")), path


def test_translation_fallbacks():
    assert t("ar", "premium.row_core") == EN["premium.row_core"]  # any locale -> English
    assert t("en", "does.not_exist") == "does.not_exist"
    assert t("en", "play.queued_many") == EN["play.queued_many"]  # missing params -> raw template, no crash
    for setting, discord_locale in (("auto", "ar"), ("ar", "en-US"), ("en", "fr"), (None, None)):
        assert resolve_locale(setting, discord_locale) == "en"


# ------------------------------------------------------------------ config


def test_config_parses_and_hides_token(tmp_path):
    settings = load_settings(
        {"DISCORD_TOKEN": FAKE_TOKEN, "PREMIUM_SKU_IDS": "1,2", "ENABLED_PROVIDERS": "youtube", "HEALTH_PORT": "0"},
        properties_path=tmp_path / "missing",
    )
    assert settings.premium_sku_ids == {1, 2}
    assert settings.enabled_providers == {"youtube"}
    assert FAKE_TOKEN not in repr(settings)


def test_config_errors_are_collected_without_secrets(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_settings({"DISCORD_TOKEN": FAKE_TOKEN, "HEALTH_PORT": "abc", "ENVIRONMENT": "prod",
                       "ENABLED_PROVIDERS": "youtube,napster", "MAX_SESSIONS_SOFT": "10", "MAX_SESSIONS_HARD": "5"},
                      properties_path=tmp_path / "missing")
    text = str(exc.value)
    assert "HEALTH_PORT" in text and "ENVIRONMENT" in text and "napster" in text and "MAX_SESSIONS_HARD" in text
    assert FAKE_TOKEN not in text


def test_config_requires_token_and_reads_legacy_properties(tmp_path):
    with pytest.raises(ConfigError):
        load_settings({}, properties_path=tmp_path / "missing")
    props = tmp_path / "properties.properties"
    props.write_text("[configs]\nBOT_TOKEN=legacy-token\n", encoding="utf-8")
    assert load_settings({}, properties_path=props).token == "legacy-token"


# ------------------------------------------------------------------ logging


def test_redaction():
    text = f"token {FAKE_TOKEN} url https://rr1---sn.googlevideo.com/videoplayback?ip=1.2.3.4&sig=abc and ?token=xyz"
    out = redact(text)
    assert FAKE_TOKEN not in out and "1.2.3.4" not in out and "xyz" not in out


def test_json_logs_carry_context():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    with bind(correlation="abc123", guild_id=42):
        from musicbot.logging_setup import _ContextFilter

        _ContextFilter().filter(record)
    line = JsonFormatter().format(record)
    assert '"correlation_id": "abc123"' in line and '"guild_id": 42' in line and "hello world" in line


# ------------------------------------------------------------------ rate limiting


def test_rate_limiter_bucket():
    limiter = RateLimiter(capacity=2, refill_per_second=1.0, max_keys=2)
    assert limiter.allow("a", now=0) and limiter.allow("a", now=0)
    assert not limiter.allow("a", now=0)
    assert limiter.retry_after("a", now=0) == pytest.approx(1.0)
    assert limiter.allow("a", now=1.0)
    limiter.allow("b", now=1)
    limiter.allow("c", now=1)
    assert len(limiter._buckets) == 2  # bounded memory


def test_dotenv_loader_handles_quotes_and_inline_comments(tmp_path, monkeypatch):
    from musicbot.config import load_dotenv_file

    env_file = tmp_path / ".env"
    env_file.write_text('# c\nSHZ_A=production   # comment\nSHZ_B="quoted # not a comment"\nSHZ_C=\n', encoding="utf-8")
    for key in ("SHZ_A", "SHZ_B", "SHZ_C"):
        monkeypatch.delenv(key, raising=False)
    load_dotenv_file(env_file)
    import os

    assert os.environ["SHZ_A"] == "production"
    assert os.environ["SHZ_B"] == "quoted # not a comment"
    assert os.environ["SHZ_C"] == ""
