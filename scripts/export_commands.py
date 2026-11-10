"""Export the bot's slash commands as JSON (offline, no Discord login).

    python scripts/export_commands.py            # writes docs/topgg/commands.json
    python scripts/export_commands.py --check    # exit 1 if the file is out of date

The output is Discord's application command format, exactly what the bot registers with
Discord. Top.gg's Commands tab and `PUT /api/v1/projects/@me/commands` take it as is.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from musicbot.bot import ShehrazadBot  # noqa: E402
from musicbot.config import Settings  # noqa: E402

OUT = ROOT / "docs" / "topgg"


async def build() -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        # The bot never logs in here; the token is a dummy so Settings validates.
        config = Settings(token="offline", database_path=Path(tmp) / "export.db", health_port=0, sync_commands=False)  # noqa: S106
        bot = ShehrazadBot(config)
        try:
            await bot.setup_hook()
            slash = sorted((command.to_dict(bot.tree) for command in bot.tree.get_commands()), key=lambda c: c["name"])
        finally:
            await bot.close()
    return {"commands.json": json.dumps(slash, indent=2, ensure_ascii=False) + "\n"}


def main() -> int:
    files = asyncio.run(build())
    if "--check" in sys.argv:
        stale = [name for name, text in files.items()
                 if not (OUT / name).is_file() or (OUT / name).read_text(encoding="utf-8") != text]
        if stale:
            print("out of date:", ", ".join(stale), "- run python scripts/export_commands.py")
            return 1
        print("command export is up to date")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
        print("wrote", (OUT / name).relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
