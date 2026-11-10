-- Initial schema. All timestamps are ISO-8601 UTC strings.

CREATE TABLE guild_settings (
    guild_id              INTEGER PRIMARY KEY,
    language              TEXT    NOT NULL DEFAULT 'auto',
    dj_role_id            INTEGER,
    music_channel_id      INTEGER,
    dj_mode               INTEGER NOT NULL DEFAULT 0,
    request_mode          TEXT    NOT NULL DEFAULT 'everyone',
    vote_skip             INTEGER NOT NULL DEFAULT 1,
    vote_skip_ratio       REAL    NOT NULL DEFAULT 0.5,
    default_volume        INTEGER NOT NULL DEFAULT 100,
    stay_connected        INTEGER NOT NULL DEFAULT 0,
    stay_channel_id       INTEGER,
    idle_timeout_seconds  INTEGER,
    setup_completed       INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT    NOT NULL,
    updated_at            TEXT    NOT NULL
);

CREATE TABLE guild_presence (
    guild_id   INTEGER PRIMARY KEY,
    joined_at  TEXT,
    left_at    TEXT
);
CREATE INDEX idx_presence_left ON guild_presence(left_at) WHERE left_at IS NOT NULL;

-- Mirror of Discord entitlements (source of truth stays Discord).
CREATE TABLE entitlements (
    id          INTEGER PRIMARY KEY,
    sku_id      INTEGER NOT NULL,
    guild_id    INTEGER,
    user_id     INTEGER,
    type        INTEGER NOT NULL,
    deleted     INTEGER NOT NULL DEFAULT 0,
    consumed    INTEGER NOT NULL DEFAULT 0,
    starts_at   TEXT,
    ends_at     TEXT,
    source      TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
CREATE INDEX idx_entitlements_guild ON entitlements(guild_id);
CREATE INDEX idx_entitlements_user ON entitlements(user_id);

-- Complimentary premium granted by an operator through the CLI (audited).
CREATE TABLE premium_grants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    reason      TEXT    NOT NULL,
    granted_by  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT,
    revoked_at  TEXT,
    revoked_by  TEXT
);
CREATE INDEX idx_grants_guild ON premium_grants(guild_id);

CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    details     TEXT
);
CREATE INDEX idx_audit_created ON audit_log(created_at);

CREATE TABLE playlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL COLLATE NOCASE,
    created_by  INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (guild_id, name)
);

CREATE TABLE playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    url         TEXT    NOT NULL,
    provider    TEXT    NOT NULL,
    duration    REAL,
    artist      TEXT,
    PRIMARY KEY (playlist_id, position)
);

CREATE TABLE favorites (
    user_id     INTEGER NOT NULL,
    url         TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    provider    TEXT    NOT NULL,
    duration    REAL,
    artist      TEXT,
    created_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, url)
);

CREATE TABLE play_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    requester_id  INTEGER,
    title         TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    provider      TEXT    NOT NULL,
    duration      REAL,
    played_at     TEXT    NOT NULL
);
CREATE INDEX idx_history_guild_time ON play_history(guild_id, played_at DESC);
CREATE INDEX idx_history_time ON play_history(played_at);

CREATE TABLE queue_snapshots (
    guild_id          INTEGER PRIMARY KEY,
    voice_channel_id  INTEGER,
    text_channel_id   INTEGER,
    payload           TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE feature_flags (
    name        TEXT PRIMARY KEY,
    enabled     INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL,
    updated_by  TEXT    NOT NULL
);

CREATE TABLE feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER,
    user_id     INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX idx_feedback_created ON feedback(created_at);

CREATE TABLE kv (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
