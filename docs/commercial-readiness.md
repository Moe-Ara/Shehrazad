# Commercial readiness report

Status as of 2026-11-10, branch `commercial-readiness`. "Implemented" means code plus automated tests
in this repository; "unverified" means it needs a live Discord/production check that could not be
run from the development machine.

## 1. Baseline (before this work)

| Area | What existed |
|---|---|
| Language / library | Python 3.12 (container), discord.py 2.7, yt-dlp, FFmpeg, PyNaCl + davey (DAVE voice E2EE) |
| Structure | One 650-line `shehrazad.py`, a `Common.py` that read `properties.properties` at import time |
| State | Module-level globals: **one queue, one "now playing", one volume for every server** |
| Audio pipeline | yt-dlp `extract_info` called **synchronously on the event loop**, FFmpeg `loudnorm`, `PCMVolumeTransformer` |
| Commands | Prefix `!play/!p/!l3be`, `!join`, `!leave`, `!skip`, `!np`, `!volume`, `!playlist`, `!shuffleplay`, `!babe`; slash `/play`, `/playlist`, `/queue`, `/nowplaying`, `/volume` |
| Persistence | None (no database) |
| Tests | None |
| Deployment | Dockerfile (root user, no healthcheck) + compose on a 1 vCPU / 2 GB Ubuntu VPS |
| Docs | README only |

The two user-reported bugs from earlier in the session (single songs queued as playlists; silent
voice caused by `davey` 0.2) were fixed before this work and are kept.

## 2. Top 10 highest-impact issues found (all addressed)

| # | Issue | Impact | Resolution |
|---|---|---|---|
| 1 | Global playback state shared by every server | Two servers playing at once corrupt each other's queue/voice; impossible to sell to more than one server | Per-guild `GuildPlayer` sessions owned by `PlayerManager` (`musicbot/player.py`, `manager.py`) |
| 2 | yt-dlp called synchronously on the event loop | Every search/playlist froze the whole bot (heartbeats, all servers) for seconds | Bounded thread pool + timeouts (`sources.py`); test proves the loop keeps ticking |
| 3 | Racy playback transitions (`after` callback calling `play_next` across threads, recursive `play_next` on errors) | Double plays, skipped tracks, stack growth on long error streaks | Single playback loop per guild, intent-based control (`_control`), lock on all mutations, `call_soon_threadsafe` |
| 4 | No error classification | Removed videos retried forever / outages reported as "song not found"; raw exception text sent to users | Transient vs permanent errors, retry once, outage pause, friendly translated messages, correlation IDs |
| 5 | Security: `nocheckcertificate`, yt-dlp generic extractor on any URL (SSRF into the VPS network), raw errors and Discord tokens possible in logs, YouTube cookies committed to git | Credential leakage, internal network probing | Certificate checks on, extractor allow-list, SSRF checks for direct URLs, log redaction, cookies no longer used |
| 6 | Channel flooding and stale controls | A message per track; old buttons kept "working" against the wrong state | One persistent Now Playing panel edited in place (rate-limited, re-posted when buried), stale panels rejected and disabled |
| 7 | No permissions model | Anyone could stop or skip anyone | DJ role, DJ mode, request permissions, vote-skip, same-channel checks, permission diagnostics |
| 8 | No persistence | Settings/playlists impossible; nothing survives restarts | SQLite (WAL) with migrations, settings, playlists, favorites, history, snapshots, audit log |
| 9 | No monetization path | Cannot sell | Entitlement service mirroring Discord Premium Apps, guild-scoped feature gates, `/premium`, operator grants |
| 10 | No operability | Blind in production | JSON logs with correlation/guild context, Prometheus metrics, `/healthz` `/readyz`, kill switches, graceful shutdown, non-root container, healthcheck |

Further defects fixed along the way: garbled `????` error strings, `VoiceProtocol` assumed connected,
unbounded queue, `PCMVolumeTransformer` values >2.0 possible via buttons, "Multiple -ac/-ar" FFmpeg
options, a deleted video ("This video is unavailable") misclassified as an outage, `/about` crashing
when no links are configured, a `QueueView._refresh` name collision with discord.py internals.

## 3. Current architecture

```
shehrazad.py / python -m musicbot ─► main.py (config validation, logging, signals)
                                        └─► bot.py  ShehrazadBot (wiring, events, background jobs, command sync)
  cogs/  music · library · settings · premium · info · legacy   (transport: slash, buttons, ! prefix)
     └─► service.py  MusicService     (authorization + behaviour; returns Reply / raises UserError)
            ├─► manager.py  PlayerManager (guild → session, voice events, panel I/O, snapshots, capacity)
            │      └─► player.py GuildPlayer (one playback loop per guild) ─► queue.py TrackQueue (pure)
            ├─► sources.py  ProviderRegistry → YouTubeProvider (yt-dlp, thread pool) / DirectProvider
            ├─► premium.py  EntitlementService (Discord entitlements + grants → Tier/limits/features)
            └─► store.py    repositories over db.py (aiosqlite, migrations)
  ops.py (flags, rate limits, metrics) · health.py (aiohttp) · logging_setup.py · i18n.py · ui.py · cli.py
```

Design rules: all SQL in `store.py` and always scoped by guild/user; all user text through `i18n.t`;
all Discord message I/O for sessions in `manager.py`; every command goes through `interactions.run`
(defer, rate-limit, error translation, metrics).

## 4. Discord platform compliance findings

Sources: official docs at docs.discord.com (Monetization overview, Enabling Monetization,
Implementing App Subscriptions, Entitlement resource). The Developer Policy and Premium Apps help
pages returned HTTP 403 to automated fetching; the points marked † come from secondary sources and
**must be re-read in the official text before launch**.

| Requirement | Status |
|---|---|
| Verified app owned by a **developer team**, team owner 18+, verified emails + 2FA | **Blocked — owner action.** App appears to be personally owned; must be transferred to a team and verified (needed at 75+ servers anyway). |
| App uses slash commands (or approved Message Content intent) | Done. Legacy `!` commands are optional and off by default. |
| Links to Terms of Service and Privacy Policy that accurately describe data use | Drafts in `docs/terms-of-service.md` / `docs/privacy-policy.md`; **must be hosted and legally reviewed**. |
| No harmful language in name/description/commands | **Action:** the legacy `!babe` easter egg contains an insult; it only loads with `LEGACY_PREFIX_COMMANDS=true`. Keep that off (or remove the command) for the public/verified app. |
| Payouts via Stripe; teams in US/EU/UK only | Owner is EU-based → eligible region. Owner action. |
| Agree to Monetization Terms + Developer Policy | Owner action. |
| Paid features must also be purchasable through Premium Apps where available (since 2024-10-07) † | Design uses Premium Apps SKUs only; no external checkout implemented. |
| Revenue share: 85% to developer less processing fees (first $1M) † | Used in the cost model; verify current terms. |
| Entitlements are the source of truth; subscription events are not | Implemented exactly this way. |
| Cancellation: no event; access until period end; `ENTITLEMENT_UPDATE` with `ends_at` when it ends; `ENTITLEMENT_DELETE` on refund | Implemented and tested (`tests/test_premium.py`). |
| Upsell via premium-style button with `sku_id` | Implemented; shown only on gates and `/premium`, and only when `premium.sales` flag is on. |
| Data minimisation / deletion † | Retention purge, export/delete via `/privacy` and CLI, deletion 30 days after removal. |

## 5. Music source licensing (deferred by owner)

Per the owner's instruction ("don't worry about the legality for now") no licensing work was done and
YouTube remains the default source. Recorded so it is not forgotten: YouTube's Terms of Service do not
permit this kind of streaming, and YouTube forced Groovy and Rythm offline in 2021. The provider
abstraction and the `provider.youtube` kill switch exist so a licensed source can be added or
YouTube disabled without code changes. This is the single largest commercial risk and must be
decided before charging customers at scale.

## 6. Prioritised plan

### P0 — required to sell (status)
- [x] Guild-isolated sessions, non-blocking extraction, race-free transitions
- [x] Error classification, no stack traces to users, secret-safe logs
- [x] Persistent panel, consistent UX, help/setup/settings/premium/status/privacy/support/invite/about
- [x] Entitlement-based premium with guild scoping and safe event handling
- [x] Database + migrations, retention, export/delete
- [x] Health, metrics, graceful shutdown, non-root container
- [ ] **Owner:** team ownership, verification, SKU creation, ToS/Privacy hosting, payouts
- [ ] **Owner:** decision on music source licensing
- [ ] Live Discord verification of the new build on a staging application (see `docs/launch-checklist.md`)

### P1 — first months
- Second, licensed or user-supplied provider (e.g. attachments are already supported).
- Off-box database backups (cron + `sqlite3 .backup`) and alerting on `/readyz` + metrics.
- Command localisation (Discord `name_localizations`) for Arabic.
- Autoplay only if an authorised recommendation source exists (currently not implemented on purpose).
- Website with ToS/Privacy pages and a status page.

### P2 — growth
- Sharding (`AutoShardedBot`) beyond ~2,000 servers; PostgreSQL when running more than one process.
- Optional audio filters were **removed at the owner's request** earlier in the session and are not reintroduced.
- Scheduled sessions, per-user subscriptions (evaluate separately; currently guild-only).

## 7. Explicit design decisions and trade-offs

- **Kept the stack** (discord.py + yt-dlp + FFmpeg) instead of Lavalink: no extra JVM service on a 2 GB box; per-guild isolation was achievable in-process.
- **SQLite over PostgreSQL**: single process, single VPS; WAL mode supports the CLI writing concurrently. Migration path in the operator guide.
- **Guild subscriptions only**: premium follows the server, matching how music sessions work. User subscriptions are deliberately not implemented.
- **Free tier never paywalls playback controls**: queue management, vote-skip, DJ roles, favorites and history are free; premium adds scale (queue/import size), persistence (saved playlists, restore), 24/7, longer idle and priority capacity.
- **Comp grants are CLI-only** (shell access to the server), audited, and need a reason — no Discord-side admin command, no hard-coded IDs.
- **Panel instead of per-track messages**; short notices auto-delete after 20 s.
- **Session caps** (default 20 free / 25 premium) sized to the measured cost of one stream on 1 vCPU.
- **Slash-first**: Message Content intent is only requested when legacy commands are enabled.
