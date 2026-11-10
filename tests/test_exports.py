"""The committed command exports (docs/topgg/*.json) must match the bot's real commands."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_exporter():
    spec = importlib.util.spec_from_file_location("export_commands", ROOT / "scripts" / "export_commands.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_command_exports_are_up_to_date():
    files = await _load_exporter().build()
    for name, text in files.items():
        committed = (ROOT / "docs" / "topgg" / name).read_text(encoding="utf-8")
        assert committed == text, f"{name} is stale - run python scripts/export_commands.py"


def test_topgg_commands_use_discord_command_structure():
    commands = json.loads((ROOT / "docs" / "topgg" / "commands.json").read_text(encoding="utf-8"))
    assert len(commands) == len({c["name"] for c in commands})
    for command in commands:
        assert command["type"] == 1 and 1 <= len(command["description"]) <= 100
        assert command["name"] == command["name"].lower() and len(command["name"]) <= 32


def test_topgg_long_description_has_no_blank_lines():
    # A blank line ends a raw-HTML block in Markdown, and the rest would render as a code block.
    text = (ROOT / "docs" / "topgg" / "long-description.html").read_text(encoding="utf-8")
    assert all(line.strip() for line in text.splitlines())
    assert "<script" not in text.lower()
