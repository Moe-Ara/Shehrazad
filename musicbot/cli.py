"""Operator CLI. Runs against the same database as the bot; changes apply live.

    python -m musicbot.cli premium grant <guild_id> --reason "owner" [--days N]
    python -m musicbot.cli premium revoke <guild_id> --reason "..."
    python -m musicbot.cli premium status <guild_id>
    python -m musicbot.cli premium list
    python -m musicbot.cli flag list | flag set <name> on|off
    python -m musicbot.cli data export-guild <id> | delete-guild <id> | export-user <id> | delete-user <id> | purge
    python -m musicbot.cli audit [--limit N]
    python -m musicbot.cli feedback [--limit N]
    python -m musicbot.cli migrate | stats

Only people with shell access to the server can run this. Every change is
written to the audit log with the operator's OS user name.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from datetime import timedelta
from typing import Any

from .config import load_dotenv_file, load_settings
from .db import Database
from .ops import KNOWN_FLAGS
from .premium import EntitlementService
from .store import AuditRepo, DataRights, EntitlementRepo, FeedbackRepo, FlagRepo, GrantRepo
from .util import utcnow


def _actor(explicit: str | None) -> str:
    if explicit:
        return f"operator:{explicit}"
    try:
        name = getpass.getuser()
    except Exception:
        name = os.environ.get("USER") or "unknown"
    return f"operator:{name}"


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m musicbot.cli", description="Shehrazad operator tools")
    parser.add_argument("--db", help="Database path (default: DATABASE_PATH or data/shehrazad.db)")
    parser.add_argument("--as", dest="actor", help="Operator name recorded in the audit log")
    sub = parser.add_subparsers(dest="cmd", required=True)

    premium = sub.add_parser("premium", help="Complimentary premium grants").add_subparsers(dest="action", required=True)
    grant = premium.add_parser("grant", help="Grant premium to a guild (no payment)")
    grant.add_argument("guild_id", type=int)
    grant.add_argument("--reason", required=True)
    grant.add_argument("--days", type=int, help="Expire after N days (default: never)")
    revoke = premium.add_parser("revoke", help="Revoke all active grants for a guild")
    revoke.add_argument("guild_id", type=int)
    revoke.add_argument("--reason", required=True)
    status = premium.add_parser("status", help="Show premium status for a guild")
    status.add_argument("guild_id", type=int)
    premium.add_parser("list", help="List all grants")

    flag = sub.add_parser("flag", help="Feature flags / kill switches").add_subparsers(dest="action", required=True)
    flag.add_parser("list")
    flag_set = flag.add_parser("set")
    flag_set.add_argument("name", choices=sorted(KNOWN_FLAGS))
    flag_set.add_argument("state", choices=["on", "off"])

    data = sub.add_parser("data", help="Data export/deletion and retention").add_subparsers(dest="action", required=True)
    for name in ("export-guild", "delete-guild", "export-user", "delete-user"):
        p = data.add_parser(name)
        p.add_argument("id", type=int)
    data.add_parser("purge", help="Apply retention rules now")

    audit = sub.add_parser("audit", help="Show the audit log")
    audit.add_argument("--limit", type=int, default=50)
    feedback = sub.add_parser("feedback", help="Show recent feedback")
    feedback.add_argument("--limit", type=int, default=20)
    sub.add_parser("migrate", help="Apply database migrations")
    sub.add_parser("stats", help="Database statistics")
    return parser


async def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    load_dotenv_file()
    settings = load_settings(require_token=False)
    db = Database(args.db or settings.database_path)
    await db.connect()
    actor = _actor(args.actor)
    audit = AuditRepo(db)
    try:
        if args.cmd == "migrate":
            print(f"schema version {await db.schema_version()}")
        elif args.cmd == "premium":
            service = EntitlementService(EntitlementRepo(db), GrantRepo(db), audit, sku_ids=settings.premium_sku_ids)
            await service.load()
            if args.action == "grant":
                expires = utcnow() + timedelta(days=args.days) if args.days else None
                grant_id = await service.grant(args.guild_id, reason=args.reason, actor=actor, expires_at=expires)
                print(f"granted premium to guild {args.guild_id} (grant #{grant_id}, expires: {expires or 'never'})")
                print("the running bot picks this up within 60 seconds; no restart needed")
            elif args.action == "revoke":
                count = await service.revoke(args.guild_id, actor=actor, reason=args.reason)
                print(f"revoked {count} grant(s) for guild {args.guild_id}")
            elif args.action == "status":
                status = service.status(args.guild_id)
                _print({"guild_id": args.guild_id, "tier": status.tier.value, "source": status.source,
                        "ends_at": status.ends_at, "grant_reason": status.grant_reason})
            elif args.action == "list":
                _print([g.__dict__ for g in await GrantRepo(db).all()])
        elif args.cmd == "flag":
            repo = FlagRepo(db)
            if args.action == "list":
                values = await repo.all()
                _print({name: {"enabled": values.get(name, True), "description": desc} for name, desc in KNOWN_FLAGS.items()})
            else:
                enabled = args.state == "on"
                await repo.set(args.name, enabled, actor)
                await audit.record(actor, "flag.set", args.name, enabled=enabled)
                print(f"{args.name} -> {args.state} (applies within 60 seconds)")
        elif args.cmd == "data":
            rights = DataRights(db)
            if args.action == "export-guild":
                _print(await rights.export_guild(args.id))
            elif args.action == "export-user":
                _print(await rights.export_user(args.id))
            elif args.action == "delete-guild":
                counts = await rights.delete_guild(args.id)
                await audit.record(actor, "data.delete_guild", str(args.id), **counts)
                _print(counts)
            elif args.action == "delete-user":
                counts = await rights.delete_user(args.id)
                await audit.record(actor, "data.delete_user", str(args.id), **counts)
                _print(counts)
            elif args.action == "purge":
                counts = await rights.purge(
                    history_days=settings.history_retention_days,
                    feedback_days=settings.feedback_retention_days,
                    departed_guild_days=settings.departed_guild_retention_days,
                )
                await audit.record(actor, "data.purge", None, **counts)
                _print(counts)
        elif args.cmd == "audit":
            _print(await audit.recent(args.limit))
        elif args.cmd == "feedback":
            _print(await FeedbackRepo(db).recent(args.limit))
        elif args.cmd == "stats":
            tables = ["guild_settings", "playlists", "playlist_tracks", "favorites", "play_history",
                      "entitlements", "premium_grants", "feedback", "audit_log", "queue_snapshots"]
            counts = {}
            for table in tables:
                row = await db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")
                counts[table] = row["n"] if row else 0
            _print({"schema_version": await db.schema_version(), "rows": counts})
    finally:
        await db.close()
    return 0


def main() -> None:
    sys.exit(asyncio.run(run(sys.argv[1:])))


if __name__ == "__main__":
    main()
