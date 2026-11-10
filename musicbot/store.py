"""Repositories: all SQL lives here. Every query is scoped by guild or user."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta
from typing import Any

from .db import Database
from .models import Track
from .util import to_iso, utcnow

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- settings


@dataclass(frozen=True)
class GuildSettings:
    guild_id: int
    language: str = "auto"  # unused since the bot became English-only; kept so old rows still load
    dj_role_id: int | None = None
    music_channel_id: int | None = None
    dj_mode: bool = False  # only DJs may control playback
    request_mode: str = "everyone"  # everyone | dj
    vote_skip: bool = True
    vote_skip_ratio: float = 0.5
    default_volume: int = 100
    stay_connected: bool = False  # 24/7 (premium)
    stay_channel_id: int | None = None
    idle_timeout_seconds: int | None = None  # None = tier default
    setup_completed: bool = False


_BOOL_FIELDS = {"dj_mode", "vote_skip", "stay_connected", "setup_completed"}
_SETTING_VALIDATORS: dict[str, Any] = {
    "language": lambda v: v in ("auto", "en", "ar"),
    "dj_role_id": lambda v: v is None or (isinstance(v, int) and v > 0),
    "music_channel_id": lambda v: v is None or (isinstance(v, int) and v > 0),
    "dj_mode": lambda v: isinstance(v, bool),
    "request_mode": lambda v: v in ("everyone", "dj"),
    "vote_skip": lambda v: isinstance(v, bool),
    "vote_skip_ratio": lambda v: isinstance(v, (int, float)) and 0.1 <= v <= 1.0,
    "default_volume": lambda v: isinstance(v, int) and 1 <= v <= 150,
    "stay_connected": lambda v: isinstance(v, bool),
    "stay_channel_id": lambda v: v is None or (isinstance(v, int) and v > 0),
    "idle_timeout_seconds": lambda v: v is None or (isinstance(v, int) and 60 <= v <= 86400),
    "setup_completed": lambda v: isinstance(v, bool),
}


class SettingsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: dict[int, GuildSettings] = {}

    async def get(self, guild_id: int) -> GuildSettings:
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        row = await self.db.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        if row is None:
            settings = GuildSettings(guild_id=guild_id)
        else:
            data = {f.name: row[f.name] for f in fields(GuildSettings)}
            for key in _BOOL_FIELDS:
                data[key] = bool(data[key])
            settings = GuildSettings(**data)
        self._cache[guild_id] = settings
        return settings

    async def update(self, guild_id: int, **changes: Any) -> GuildSettings:
        for key, value in changes.items():
            validator = _SETTING_VALIDATORS.get(key)
            if validator is None:
                raise ValueError(f"unknown setting {key}")
            if not validator(value):
                raise ValueError(f"invalid value for {key}")
        current = await self.get(guild_id)
        updated = replace(current, **changes)
        now = to_iso(utcnow())
        values = {f.name: getattr(updated, f.name) for f in fields(GuildSettings)}
        for key in _BOOL_FIELDS:
            values[key] = int(values[key])
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        assignments = ", ".join(f"{k} = excluded.{k}" for k in values if k != "guild_id")
        await self.db.execute(
            f"INSERT INTO guild_settings ({columns}, created_at, updated_at) "
            f"VALUES ({placeholders}, ?, ?) "
            f"ON CONFLICT(guild_id) DO UPDATE SET {assignments}, updated_at = excluded.updated_at",
            (*values.values(), now, now),
        )
        self._cache[guild_id] = updated
        return updated

    async def reset(self, guild_id: int) -> GuildSettings:
        await self.db.execute("DELETE FROM guild_settings WHERE guild_id = ?", (guild_id,))
        self._cache.pop(guild_id, None)
        return await self.get(guild_id)

    def forget(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)


# --------------------------------------------------------------------------- library


@dataclass(frozen=True)
class PlaylistSummary:
    id: int
    name: str
    track_count: int
    created_by: int
    updated_at: str


class PlaylistRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list_all(self, guild_id: int) -> list[PlaylistSummary]:
        rows = await self.db.fetchall(
            "SELECT p.id, p.name, p.created_by, p.updated_at, COUNT(t.position) AS n "
            "FROM playlists p LEFT JOIN playlist_tracks t ON t.playlist_id = p.id "
            "WHERE p.guild_id = ? GROUP BY p.id ORDER BY p.name",
            (guild_id,),
        )
        return [
            PlaylistSummary(r["id"], r["name"], r["n"], r["created_by"], r["updated_at"])
            for r in rows
        ]

    async def count(self, guild_id: int) -> int:
        row = await self.db.fetchone("SELECT COUNT(*) AS n FROM playlists WHERE guild_id = ?", (guild_id,))
        return int(row["n"]) if row else 0

    async def find(self, guild_id: int, name: str) -> PlaylistSummary | None:
        for playlist in await self.list_all(guild_id):
            if playlist.name.lower() == name.strip().lower():
                return playlist
        return None

    async def save(self, guild_id: int, name: str, created_by: int, tracks: list[Track]) -> int:
        """Create or overwrite a playlist atomically. Returns the playlist id."""
        now = to_iso(utcnow())
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT id FROM playlists WHERE guild_id = ? AND name = ?", (guild_id, name)
            )
            row = await cursor.fetchone()
            if row is None:
                cursor = await conn.execute(
                    "INSERT INTO playlists (guild_id, name, created_by, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (guild_id, name, created_by, now, now),
                )
                playlist_id = int(cursor.lastrowid or 0)
            else:
                playlist_id = int(row["id"])
                await conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
                await conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
            await conn.executemany(
                "INSERT INTO playlist_tracks (playlist_id, position, title, url, provider, duration, artist) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (playlist_id, i, t.title, t.url, t.provider, t.duration, t.artist)
                    for i, t in enumerate(tracks)
                ],
            )
        return playlist_id

    async def tracks(self, guild_id: int, playlist_id: int) -> list[Track]:
        rows = await self.db.fetchall(
            "SELECT t.* FROM playlist_tracks t JOIN playlists p ON p.id = t.playlist_id "
            "WHERE p.guild_id = ? AND p.id = ? ORDER BY t.position",
            (guild_id, playlist_id),
        )
        return [
            Track(title=r["title"], url=r["url"], provider=r["provider"], duration=r["duration"], artist=r["artist"])
            for r in rows
        ]

    async def delete(self, guild_id: int, playlist_id: int) -> bool:
        return await self.db.execute(
            "DELETE FROM playlists WHERE guild_id = ? AND id = ?", (guild_id, playlist_id)
        ) > 0


class FavoritesRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, user_id: int, track: Track) -> bool:
        changed = await self.db.execute(
            "INSERT OR IGNORE INTO favorites (user_id, url, title, provider, duration, artist, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, track.url, track.title, track.provider, track.duration, track.artist, to_iso(utcnow())),
        )
        return changed > 0

    async def remove(self, user_id: int, url: str) -> bool:
        return await self.db.execute(
            "DELETE FROM favorites WHERE user_id = ? AND url = ?", (user_id, url)
        ) > 0

    async def count(self, user_id: int) -> int:
        row = await self.db.fetchone("SELECT COUNT(*) AS n FROM favorites WHERE user_id = ?", (user_id,))
        return int(row["n"]) if row else 0

    async def list_all(self, user_id: int, limit: int = 100) -> list[Track]:
        rows = await self.db.fetchall(
            "SELECT * FROM favorites WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
        )
        return [
            Track(title=r["title"], url=r["url"], provider=r["provider"], duration=r["duration"], artist=r["artist"])
            for r in rows
        ]


class HistoryRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, guild_id: int, track: Track) -> None:
        await self.db.execute(
            "INSERT INTO play_history (guild_id, requester_id, title, url, provider, duration, played_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, track.requester_id, track.title, track.url, track.provider, track.duration, to_iso(utcnow())),
        )

    async def recent(self, guild_id: int, limit: int = 25) -> list[Track]:
        rows = await self.db.fetchall(
            "SELECT title, url, provider, duration, MAX(played_at) AS last_played "
            "FROM play_history WHERE guild_id = ? GROUP BY url ORDER BY last_played DESC LIMIT ?",
            (guild_id, limit),
        )
        return [Track(title=r["title"], url=r["url"], provider=r["provider"], duration=r["duration"]) for r in rows]

    async def search(self, guild_id: int, user_id: int, text: str, limit: int = 10) -> list[Track]:
        """Autocomplete source: the user's favorites, then this server's history."""
        like = f"%{text.strip()}%"
        rows = await self.db.fetchall(
            "SELECT title, url, provider, duration FROM ("
            "  SELECT title, url, provider, duration, 0 AS rank, created_at AS ts FROM favorites"
            "  WHERE user_id = ? AND title LIKE ?"
            "  UNION ALL"
            "  SELECT title, url, provider, duration, 1 AS rank, played_at AS ts FROM play_history"
            "  WHERE guild_id = ? AND title LIKE ?"
            ") ORDER BY rank, ts DESC LIMIT ?",
            (user_id, like, guild_id, like, limit * 3),
        )
        seen, results = set(), []
        for r in rows:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            results.append(Track(title=r["title"], url=r["url"], provider=r["provider"], duration=r["duration"]))
            if len(results) >= limit:
                break
        return results


# --------------------------------------------------------------------------- snapshots


class SnapshotRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def save(self, guild_id: int, voice_channel_id: int | None, text_channel_id: int | None, payload: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO queue_snapshots (guild_id, voice_channel_id, text_channel_id, payload, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
            "voice_channel_id = excluded.voice_channel_id, text_channel_id = excluded.text_channel_id, "
            "payload = excluded.payload, updated_at = excluded.updated_at",
            (guild_id, voice_channel_id, text_channel_id, json.dumps(payload), to_iso(utcnow())),
        )

    async def get(self, guild_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM queue_snapshots WHERE guild_id = ?", (guild_id,))
        if row is None:
            return None
        return {
            "voice_channel_id": row["voice_channel_id"],
            "text_channel_id": row["text_channel_id"],
            "payload": json.loads(row["payload"]),
            "updated_at": row["updated_at"],
        }

    async def all(self) -> list[int]:
        rows = await self.db.fetchall("SELECT guild_id FROM queue_snapshots")
        return [int(r["guild_id"]) for r in rows]

    async def delete(self, guild_id: int) -> None:
        await self.db.execute("DELETE FROM queue_snapshots WHERE guild_id = ?", (guild_id,))


# --------------------------------------------------------------------------- premium


@dataclass(frozen=True)
class EntitlementRecord:
    id: int
    sku_id: int
    guild_id: int | None
    user_id: int | None
    type: int
    deleted: bool
    consumed: bool
    starts_at: str | None
    ends_at: str | None
    source: str


class EntitlementRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _from_row(r: Any) -> EntitlementRecord:
        return EntitlementRecord(
            id=r["id"], sku_id=r["sku_id"], guild_id=r["guild_id"], user_id=r["user_id"],
            type=r["type"], deleted=bool(r["deleted"]), consumed=bool(r["consumed"]),
            starts_at=r["starts_at"], ends_at=r["ends_at"], source=r["source"],
        )

    async def get(self, entitlement_id: int) -> EntitlementRecord | None:
        row = await self.db.fetchone("SELECT * FROM entitlements WHERE id = ?", (entitlement_id,))
        return self._from_row(row) if row else None

    async def all(self) -> list[EntitlementRecord]:
        return [self._from_row(r) for r in await self.db.fetchall("SELECT * FROM entitlements")]

    async def upsert(self, rec: EntitlementRecord) -> None:
        await self.db.execute(
            "INSERT INTO entitlements (id, sku_id, guild_id, user_id, type, deleted, consumed, starts_at, ends_at, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "sku_id = excluded.sku_id, guild_id = excluded.guild_id, user_id = excluded.user_id, type = excluded.type, "
            "deleted = excluded.deleted, consumed = excluded.consumed, starts_at = excluded.starts_at, "
            "ends_at = excluded.ends_at, source = excluded.source, updated_at = excluded.updated_at",
            (
                rec.id, rec.sku_id, rec.guild_id, rec.user_id, rec.type, int(rec.deleted), int(rec.consumed),
                rec.starts_at, rec.ends_at, rec.source, to_iso(utcnow()),
            ),
        )


@dataclass(frozen=True)
class Grant:
    id: int
    guild_id: int
    reason: str
    granted_by: str
    created_at: str
    expires_at: str | None
    revoked_at: str | None
    revoked_by: str | None

    def active(self, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > to_iso(now)  # type: ignore[operator]


class GrantRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _from_row(r: Any) -> Grant:
        return Grant(r["id"], r["guild_id"], r["reason"], r["granted_by"], r["created_at"], r["expires_at"], r["revoked_at"], r["revoked_by"])

    async def all(self) -> list[Grant]:
        return [self._from_row(r) for r in await self.db.fetchall("SELECT * FROM premium_grants ORDER BY id")]

    async def for_guild(self, guild_id: int) -> list[Grant]:
        rows = await self.db.fetchall("SELECT * FROM premium_grants WHERE guild_id = ? ORDER BY id", (guild_id,))
        return [self._from_row(r) for r in rows]

    async def create(self, guild_id: int, reason: str, granted_by: str, expires_at: datetime | None) -> int:
        return await self.db.insert(
            "INSERT INTO premium_grants (guild_id, reason, granted_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, reason, granted_by, to_iso(utcnow()), to_iso(expires_at)),
        )

    async def revoke(self, guild_id: int, revoked_by: str) -> int:
        return await self.db.execute(
            "UPDATE premium_grants SET revoked_at = ?, revoked_by = ? WHERE guild_id = ? AND revoked_at IS NULL",
            (to_iso(utcnow()), revoked_by, guild_id),
        )


class AuditRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, actor: str, action: str, target: str | None = None, **details: Any) -> None:
        await self.db.execute(
            "INSERT INTO audit_log (created_at, actor, action, target, details) VALUES (?, ?, ?, ?, ?)",
            (to_iso(utcnow()), actor, action, target, json.dumps(details, default=str) if details else None),
        )

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


class FlagRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def all(self) -> dict[str, bool]:
        rows = await self.db.fetchall("SELECT name, enabled FROM feature_flags")
        return {r["name"]: bool(r["enabled"]) for r in rows}

    async def set(self, name: str, enabled: bool, actor: str) -> None:
        await self.db.execute(
            "INSERT INTO feature_flags (name, enabled, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at, "
            "updated_by = excluded.updated_by",
            (name, int(enabled), to_iso(utcnow()), actor),
        )

    async def delete(self, name: str) -> None:
        await self.db.execute("DELETE FROM feature_flags WHERE name = ?", (name,))


class KVRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, key: str) -> str | None:
        row = await self.db.fetchone("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


class FeedbackRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, guild_id: int | None, user_id: int, kind: str, message: str) -> int:
        return await self.db.insert(
            "INSERT INTO feedback (guild_id, user_id, kind, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, kind, message, to_iso(utcnow())),
        )

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- presence & data rights


class PresenceRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def joined(self, guild_id: int) -> None:
        now = to_iso(utcnow())
        await self.db.execute(
            "INSERT INTO guild_presence (guild_id, joined_at, left_at) VALUES (?, ?, NULL) "
            "ON CONFLICT(guild_id) DO UPDATE SET left_at = NULL",
            (guild_id, now),
        )

    async def left(self, guild_id: int) -> None:
        now = to_iso(utcnow())
        await self.db.execute(
            "INSERT INTO guild_presence (guild_id, joined_at, left_at) VALUES (?, NULL, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET left_at = excluded.left_at",
            (guild_id, now),
        )


GUILD_TABLES = ("guild_settings", "play_history", "queue_snapshots", "guild_presence")


class DataRights:
    """Export and deletion of stored personal/server data, plus retention purges."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def export_guild(self, guild_id: int) -> dict[str, Any]:
        data: dict[str, Any] = {"guild_id": guild_id, "exported_at": to_iso(utcnow())}
        for table in GUILD_TABLES:
            rows = await self.db.fetchall(f"SELECT * FROM {table} WHERE guild_id = ?", (guild_id,))
            data[table] = [dict(r) for r in rows]
        playlists = await self.db.fetchall("SELECT * FROM playlists WHERE guild_id = ?", (guild_id,))
        data["playlists"] = []
        for p in playlists:
            tracks = await self.db.fetchall(
                "SELECT position, title, url, provider, duration, artist FROM playlist_tracks "
                "WHERE playlist_id = ? ORDER BY position",
                (p["id"],),
            )
            data["playlists"].append({**dict(p), "tracks": [dict(t) for t in tracks]})
        return data

    async def delete_guild(self, guild_id: int) -> dict[str, int]:
        counts: dict[str, int] = {}
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM playlists WHERE guild_id = ?", (guild_id,))
            counts["playlists"] = cursor.rowcount
            for table in GUILD_TABLES:
                cursor = await conn.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
                counts[table] = cursor.rowcount
        return counts

    async def export_user(self, user_id: int) -> dict[str, Any]:
        favorites = await self.db.fetchall("SELECT * FROM favorites WHERE user_id = ?", (user_id,))
        history = await self.db.fetchall(
            "SELECT guild_id, title, url, played_at FROM play_history WHERE requester_id = ?", (user_id,)
        )
        feedback = await self.db.fetchall("SELECT guild_id, kind, message, created_at FROM feedback WHERE user_id = ?", (user_id,))
        playlists = await self.db.fetchall("SELECT guild_id, name, created_at FROM playlists WHERE created_by = ?", (user_id,))
        return {
            "user_id": user_id,
            "exported_at": to_iso(utcnow()),
            "favorites": [dict(r) for r in favorites],
            "play_history": [dict(r) for r in history],
            "feedback": [dict(r) for r in feedback],
            "playlists_created": [dict(r) for r in playlists],
        }

    async def delete_user(self, user_id: int) -> dict[str, int]:
        counts: dict[str, int] = {}
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM favorites WHERE user_id = ?", (user_id,))
            counts["favorites"] = cursor.rowcount
            cursor = await conn.execute("UPDATE play_history SET requester_id = NULL WHERE requester_id = ?", (user_id,))
            counts["play_history_anonymized"] = cursor.rowcount
            cursor = await conn.execute("DELETE FROM feedback WHERE user_id = ?", (user_id,))
            counts["feedback"] = cursor.rowcount
            # Server playlists belong to the server; drop the creator reference.
            cursor = await conn.execute("UPDATE playlists SET created_by = 0 WHERE created_by = ?", (user_id,))
            counts["playlists_anonymized"] = cursor.rowcount
        return counts

    async def purge(
        self,
        *,
        history_days: int,
        feedback_days: int,
        departed_guild_days: int,
        audit_days: int = 365,
    ) -> dict[str, int]:
        now = utcnow()
        counts = {
            "play_history": await self.db.execute(
                "DELETE FROM play_history WHERE played_at < ?", (to_iso(now - timedelta(days=history_days)),)
            ),
            "feedback": await self.db.execute(
                "DELETE FROM feedback WHERE created_at < ?", (to_iso(now - timedelta(days=feedback_days)),)
            ),
            "audit_log": await self.db.execute(
                "DELETE FROM audit_log WHERE created_at < ?", (to_iso(now - timedelta(days=audit_days)),)
            ),
        }
        cutoff = to_iso(now - timedelta(days=departed_guild_days))
        departed = await self.db.fetchall(
            "SELECT guild_id FROM guild_presence WHERE left_at IS NOT NULL AND left_at < ?", (cutoff,)
        )
        counts["departed_guilds"] = len(departed)
        for row in departed:
            await self.delete_guild(int(row["guild_id"]))
        return counts
