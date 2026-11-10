# Shehrazad — Discord music bot

[![Top.gg votes](https://top.gg/api/v1/widgets/small/votes/discord/bot/892731315928047637)](https://top.gg/bot/892731315928047637)
[![Top.gg](https://top.gg/api/v1/widgets/small/social/discord/bot/892731315928047637)](https://top.gg/bot/892731315928047637)

Search or paste a link, and control everything from one player panel. Multi-server, slash-first,
with a free tier that never paywalls playback and a per-server Premium tier built on Discord's
Premium Apps. English only.

## Features
- `/play` by name, YouTube/SoundCloud link, playlist link or uploaded audio file (song links with
  `&list=` play just that song; `/playlist` queues the whole list)
- Persistent Now Playing panel: back, pause/resume, skip, stop, repeat, shuffle, volume, queue, like
- Queue: view (paginated), remove, move, jump, shuffle, clear, seek, previous, repeat track/queue
- Vote-skip, DJ role, DJ mode, request permissions, `/setup` wizard with permission diagnostics
- Favorites, recently played, saved server playlists ✨, 24/7 ✨, queue restore ✨
- `/anime picture` and `/anime react`: cute, safe-for-work anime pictures and reaction GIFs (nekos.best)
- `/help`, `/premium`, `/status`, `/privacy` (export/delete), `/feedback`, `/invite`, `/support`, `/about`
- Slash commands only: no prefix commands and no Message Content intent

Full list: [docs/commands.md](docs/commands.md). Tiers: [docs/premium-feature-matrix.md](docs/premium-feature-matrix.md).

## Run it

```bash
cp .env.example .env        # set DISCORD_TOKEN
mkdir -p data && sudo chown 10001:10001 data
docker compose up -d --build
```

Without Docker (Python 3.12+, FFmpeg and libopus installed):

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
python -m musicbot          # or: python shehrazad.py
```

A `properties.properties` file with `[configs] BOT_TOKEN=…` still works if `DISCORD_TOKEN` is unset.

## Give your own server Premium for free

```bash
docker compose exec shehrazad python -m musicbot.cli premium grant <server_id> --reason "owner"
```

Never expires, audited, applies within a minute. Details and the Discord-native alternatives:
[docs/operator-guide.md](docs/operator-guide.md#premium-for-your-own-servers-lifetime-no-payment).

## Develop

```bash
pip install -r requirements-dev.txt
pytest            # 129 tests, no network or Discord needed
ruff check .
mypy musicbot
```

Layout: `musicbot/` (bot.py wiring · service.py behaviour · player.py/queue.py playback ·
sources.py providers · premium.py entitlements · store.py/db.py persistence · cogs/ commands ·
ui.py embeds/views · i18n.py text · health.py/ops.py operations · cli.py operator tools).

## Docs
- [Commercial readiness report](docs/commercial-readiness.md) — audit, top issues, compliance, plan
- [Launch checklist](docs/launch-checklist.md)
- [Premium feature matrix](docs/premium-feature-matrix.md)
- [Cost model](docs/commercial-cost-model.md)
- [Operator guide](docs/operator-guide.md) — deploy, staging, backups, monitoring, incidents, scaling
- [Command reference](docs/commands.md)
- Drafts needing legal review: [Terms](docs/terms-of-service.md), [Privacy](docs/privacy-policy.md), [App Directory copy](docs/app-directory.md)
