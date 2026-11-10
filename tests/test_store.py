from datetime import timedelta

import pytest

from musicbot.db import Database, discover_migrations
from musicbot.store import (
    DataRights,
    FavoritesRepo,
    FeedbackRepo,
    HistoryRepo,
    PlaylistRepo,
    PresenceRepo,
    SettingsRepo,
    SnapshotRepo,
)
from musicbot.util import to_iso, utcnow
from tests.conftest import make_track


async def test_migrations_apply_once_and_are_versioned(tmp_path):
    path = tmp_path / "m.db"
    db = Database(path)
    await db.connect()
    version = await db.schema_version()
    assert version == max(v for v, _, _ in discover_migrations())
    assert await db.migrate() == []  # idempotent
    await db.close()
    again = Database(path)
    await again.connect()
    assert await again.schema_version() == version
    await again.close()


async def test_settings_validation_and_persistence(db, tmp_path):
    repo = SettingsRepo(db)
    assert (await repo.get(1)).default_volume == 100
    await repo.update(1, dj_role_id=55, dj_mode=True, language="ar")
    with pytest.raises(ValueError):
        await repo.update(1, default_volume=999)
    with pytest.raises(ValueError):
        await repo.update(1, not_a_setting=1)
    fresh = SettingsRepo(db)  # simulates a restart: no cache
    settings = await fresh.get(1)
    assert settings.dj_role_id == 55 and settings.dj_mode is True and settings.language == "ar"
    assert (await fresh.get(2)).dj_mode is False  # other guilds unaffected
    reset = await fresh.reset(1)
    assert reset.dj_role_id is None


async def test_playlists_are_guild_isolated_and_overwrite_atomically(db):
    repo = PlaylistRepo(db)
    await repo.save(1, "Chill", 10, [make_track(1), make_track(2)])
    await repo.save(2, "Chill", 20, [make_track(3)])
    pl = await repo.find(1, "chill")  # case-insensitive
    assert pl and pl.track_count == 2
    await repo.save(1, "Chill", 10, [make_track(9)])
    assert [t.title for t in await repo.tracks(1, pl.id)] == ["Track 9"]
    other = await repo.find(2, "Chill")
    assert await repo.tracks(1, other.id) == []  # can't read another guild's playlist by id
    assert not await repo.delete(1, other.id)
    assert await repo.count(1) == 1


async def test_history_favorites_and_autocomplete(db):
    history, favs = HistoryRepo(db), FavoritesRepo(db)
    await history.record(1, make_track("abc", requester=7))
    await history.record(1, make_track("abc", requester=7))
    await history.record(2, make_track("zzz"))
    assert [t.title for t in await history.recent(1)] == ["Track abc"]
    assert await favs.add(7, make_track("fav"))
    assert not await favs.add(7, make_track("fav"))
    results = await history.search(1, 7, "Track")
    assert results[0].title == "Track fav"  # favorites first
    assert "Track zzz" not in [t.title for t in results]  # other guild's history excluded


async def test_snapshots(db):
    repo = SnapshotRepo(db)
    await repo.save(1, 5, 6, {"upcoming": []})
    assert (await repo.get(1))["voice_channel_id"] == 5
    assert await repo.all() == [1]
    await repo.delete(1)
    assert await repo.get(1) is None


async def test_user_export_and_delete(db):
    rights = DataRights(db)
    await FavoritesRepo(db).add(7, make_track(1))
    await HistoryRepo(db).record(1, make_track(2, requester=7))
    await FeedbackRepo(db).add(1, 7, "bug", "it broke")
    export = await rights.export_user(7)
    assert len(export["favorites"]) == 1 and len(export["play_history"]) == 1 and len(export["feedback"]) == 1
    counts = await rights.delete_user(7)
    assert counts["favorites"] == 1 and counts["play_history_anonymized"] == 1
    after = await rights.export_user(7)
    assert after["favorites"] == [] and after["play_history"] == [] and after["feedback"] == []


async def test_guild_delete_and_retention_purge(db):
    rights = DataRights(db)
    await SettingsRepo(db).update(1, dj_mode=True)
    await PlaylistRepo(db).save(1, "x", 1, [make_track(1)])
    await HistoryRepo(db).record(1, make_track(1))
    await SettingsRepo(db).update(2, dj_mode=True)
    counts = await rights.delete_guild(1)
    assert counts["playlists"] == 1 and counts["guild_settings"] == 1
    assert (await rights.export_guild(2))["guild_settings"]  # other guild untouched

    old = to_iso(utcnow() - timedelta(days=40))
    await db.execute("INSERT INTO play_history (guild_id, title, url, provider, played_at) VALUES (3, 't', 'u', 'youtube', ?)", (old,))
    presence = PresenceRepo(db)
    await presence.left(2)
    await db.execute("UPDATE guild_presence SET left_at = ? WHERE guild_id = 2", (old,))
    counts = await rights.purge(history_days=30, feedback_days=90, departed_guild_days=30)
    assert counts["play_history"] == 1 and counts["departed_guilds"] == 1
    assert (await rights.export_guild(2))["guild_settings"] == []
