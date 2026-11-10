# Operator guide

## Deploy

Requirements: Docker + Compose v2 on Linux, outbound HTTPS and UDP.

```bash
git clone https://github.com/Moe-Ara/Shehrazad.git /opt/shehrazad && cd /opt/shehrazad
cp .env.example .env && chmod 600 .env     # set DISCORD_TOKEN at minimum
mkdir -p data && sudo chown 10001:10001 data   # container runs as uid 10001
docker compose up -d --build
docker compose logs -f                        # wait for "ready as …"
curl -s localhost:8080/readyz                 # {"ready": true, ...}
```

Migrations run automatically at startup (`schema_migrations` table). Run them manually with
`docker compose exec shehrazad python -m musicbot.cli migrate`.

### Current VPS (216.173.77.168)

Production runs **2.1.0** (since 2026-11-10) from `/opt/shehrazad`: image `shehrazad:2.1.0`, container
`shehrazad-shehrazad-1`. Its `.env` (mode 600) holds `DISCORD_TOKEN`, `APPLICATION_ID`,
`ENVIRONMENT=production`, `LOG_JSON=true`, `IMAGE_TAG`, `OWNER_IDS` (the owner's user ID) and
`NOVELTY_GUILD_IDS` (the owner's three servers). Add `TOPGG_TOKEN` to report the server count to top.gg, and `WEBSITE_URL=https://moe-ara.github.io/Shehrazad/`,
`TERMS_URL=https://moe-ara.github.io/Shehrazad/terms.html`, `PRIVACY_URL=https://moe-ara.github.io/Shehrazad/privacy.html` so `/about` and `/privacy` link to the site. The token must be in `.env`: the container runs as
uid 10001 and cannot read the root-only `properties.properties`. Lifetime premium grants #1-#3 cover
the owner's three servers.

Updating: `git archive -o release.tgz HEAD -- . ':!cookies.txt' ':!assets'`, upload it, then run
[`scripts/update_release.sh`](../scripts/update_release.sh) `release.tgz <new tag>` on the server (a copy is in `/root/update_v2.sh`). It backs up the current source to
`/opt/shehrazad-src-<old tag>.tgz` and replaces the source (keeping `.env`, `data/` and
`properties.properties`). It then builds and starts the new image and waits for `/readyz`. If the new
version doesn't become ready, it restores the previous source and image on its own. Previous images
stay in Docker, so a manual rollback is setting `IMAGE_TAG` back and running `docker compose up -d`.

v1 is still kept at `/opt/shehrazad.v1` (image `shehrazad-shehrazad:latest`), and the database as it
was before v2's first start is at `/opt/shehrazad-v2-prestart.db`. Rollback to v1:
`cd /opt/shehrazad && docker compose down && cd /opt && mv shehrazad shehrazad.v2 &&
mv shehrazad.v1 shehrazad && cd shehrazad && docker compose up -d`. v1 ignores v2's data.

## Configuration

Everything is environment-driven; see `.env.example` for every variable. The process refuses to start
with a clear list of problems if configuration is invalid (secrets are never printed).

## Staging vs production

Use a **separate Discord application** for staging (its own token), never the production token:

```bash
cp .env.example .env.staging   # ENVIRONMENT=staging, DEV_GUILD_ID=<test server>, HEALTH_HOST_PORT=8081
docker compose -p shehrazad-staging --env-file .env.staging up -d --build
```

`DEV_GUILD_ID` syncs commands to one server instantly instead of globally. Each compose project gets
its own `data/` only if you run it from its own directory — keep staging in `/opt/shehrazad-staging`.

## Premium for your own servers (lifetime, no payment)

**Option A — works today, before monetization is approved.** Comp grants live in the bot's database,
are written to the audit log with your name and reason, never expire unless you pass `--days`, and
apply to the running bot within 60 seconds:

```bash
docker compose exec shehrazad python -m musicbot.cli --as moe premium grant <YOUR_SERVER_ID> --reason "owner"
docker compose exec shehrazad python -m musicbot.cli premium status <YOUR_SERVER_ID>
docker compose exec shehrazad python -m musicbot.cli premium list
```

Get the server ID with Discord Developer Mode → right-click the server → Copy Server ID. Repeat for
each server you own. Only someone with shell access to the server can run this.

**Option B — once you have a SKU (after monetization is enabled).** Use Discord's own mechanisms:

- *Team-member purchase:* developer team members can buy the app's SKUs with a 100% discount.
  This creates a real subscription entitlement for the server, which the bot recognises like any other.
- *Test entitlement* (valid in perpetuity until deleted):
  ```bash
  curl -X POST https://discord.com/api/v10/applications/<APP_ID>/entitlements \
    -H "Authorization: Bot $DISCORD_TOKEN" -H "Content-Type: application/json" \
    -d '{"sku_id":"<SKU_ID>","owner_id":"<YOUR_SERVER_ID>","owner_type":1}'
  ```
  The bot sees it on the next interaction or reconciliation (≤ 30 min).

Revoke a comp grant with `premium revoke <id> --reason "..."`.

## Operator CLI

`docker compose exec shehrazad python -m musicbot.cli <command>` (or `python -m musicbot.cli` locally):

| Command | Purpose |
|---|---|
| `premium grant/revoke/status/list` | Complimentary premium (audited) |
| `flag list` / `flag set <name> on|off` | Kill switches: `provider.youtube`, `provider.direct`, `premium.sales`, `feature.saved_playlists`, `feature.stay_connected`, `feature.feedback`, `feature.anime` |
| `data export-guild/delete-guild/export-user/delete-user <id>` | Data-subject requests |
| `data purge` | Apply retention now (also runs daily) |
| `audit [--limit N]` | Administrative actions |
| `feedback [--limit N]` | Recent `/feedback` submissions |
| `stats`, `migrate` | Row counts, schema version |

Flags apply within 60 seconds, without a restart.

## Monitoring and alerting

- `GET /healthz` — liveness (Docker healthcheck uses it). 503 when the gateway has been down >5 min.
- `GET /readyz` — gateway + database; includes sessions and last entitlement reconcile.
- `GET /metrics` — Prometheus. Bound to `127.0.0.1` by compose; scrape locally or via a tunnel.

Suggested alerts:

| Alert | Condition |
|---|---|
| Bot down | `/healthz` not 200 for 2 min, or container restarting |
| Gateway disconnected | `shz_gateway_connected == 0` for 5 min |
| Source outage | `rate(shz_source_errors_total{kind="transient"}[10m]) > 0.2` |
| Entitlements stale | `increase(shz_entitlement_reconcile_failures_total[2h]) >= 3` |
| Capacity | `shz_voice_sessions >= MAX_SESSIONS_SOFT * 0.9` |
| Command errors | `rate(shz_commands_total{outcome="error"}[10m]) > 0.05` |

Logs: set `LOG_JSON=true`. Every line carries `correlation_id` (also shown to users as `ref`) and
`guild_id`. Tokens, signed media URLs and secret query parameters are redacted.

## Incidents

| Symptom | Action |
|---|---|
| Users report "music source is having trouble" | Check `/metrics` source errors and `docker compose logs`. Usually YouTube changed something → rebuild to update yt-dlp: `docker compose build --pull --no-cache && docker compose up -d`. To stop retries meanwhile: `cli flag set provider.youtube off`. |
| Bot joins but nobody hears audio | Check `pip list` in the container for `davey` 0.1.x (0.2 breaks discord.py 2.7 voice). |
| Premium missing for a paying server | `cli premium status <id>`; check `/readyz` → `entitlements_last_reconcile`; restart forces a reconcile after 30 s. |
| Abuse via feedback | `cli flag set feature.feedback off` |
| Capacity reached | Raise `MAX_SESSIONS_*` only after checking CPU; add a VPS (see scaling). |

## Backups and restore

The only state is `data/shehrazad.db` (SQLite, WAL).

```bash
# daily, e.g. from cron on the host
docker compose exec -T shehrazad python -c "import sqlite3; s=sqlite3.connect('/app/data/shehrazad.db'); d=sqlite3.connect('/app/data/backup.db'); s.backup(d); d.close()"
cp data/backup.db /backups/shehrazad-$(date +%F).db   # then ship off the box
```

Restore: stop the container, replace `data/shehrazad.db` with the backup, delete `-wal`/`-shm` files,
start. Entitlements self-heal from Discord on the next reconciliation.

## Scaling

- **One process** comfortably serves ~2,000 servers on the gateway; the limit is concurrent voice
  sessions (~20 per vCPU, see the cost model).
- **More voice capacity:** move to a bigger VPS first (CPU-bound, linear).
- **Sharding (2,500+ servers, required by Discord):** switch `commands.Bot` to `commands.AutoShardedBot`
  in `bot.py`. For multiple processes/hosts, move from SQLite to PostgreSQL (schema is portable:
  replace `aiosqlite` with `asyncpg` in `db.py`, keep repository interfaces) and run one process per
  shard range with `shard_ids=`.
- Command sync is hash-gated, so restarts don't burn Discord's daily command-update limit.

## Graceful shutdown

`SIGTERM` (docker stop) → stop background jobs → snapshot premium queues → end sessions and disconnect
voice → close the gateway → close the database. `stop_grace_period` is 30 s.
