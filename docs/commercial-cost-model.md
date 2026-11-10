# Commercial cost model

All figures are estimates. Measured values come from the production VPS on 2026-11-09/10;
everything else is labelled as an assumption. Re-measure with `/metrics` and `docker stats` once
real traffic exists.

## 1. Measured per-session resource use

| Resource | Value | Source |
|---|---|---|
| Bot process idle | ~93 MB RAM | `docker stats` on the VPS |
| FFmpeg per stream (with `loudnorm`) | ~2.5% of one core; ~30-40 MB RAM | `docker top` CPU time over 2 min of playback |
| Opus encoding + packetisation (Python thread) | ~1-2% of one core | assumption (libopus, 20 ms frames) |
| Outbound to Discord | ~128 kbit/s ≈ **58 MB / session-hour** | discord.py default encoder bitrate |
| Inbound from YouTube | 130 kbit/s audio-only (~60 MB/h) up to ~500 kbit/s when only format 18 is offered (~225 MB/h) | format seen on the VPS: itag 18 |
| Extraction (search/playlist) | short CPU bursts, 1-3 s wall time | yt-dlp, thread pool of 4 |

**Rule of thumb:** ~5% of a core and ~45 MB per concurrent session. A 1 vCPU / 2 GB VPS handles
~15-20 concurrent sessions with headroom, hence the defaults `MAX_SESSIONS_SOFT=20`,
`MAX_SESSIONS_HARD=25`.

## 2. Infrastructure cost per concurrent session

| Item | Assumption | Monthly |
|---|---|---|
| VPS 1 vCPU / 2 GB / 20 TB traffic | €5–10 (typical EU provider) | €5–10 |
| Capacity | 20 concurrent sessions | — |
| **Cost per concurrent-session slot** | | **€0.25–0.50 / month** |
| Traffic per slot used 24/7 | 58 MB/h out + up to 225 MB/h in ≈ 200 GB/month | within typical included traffic |
| Backups (off-box object storage) | < 1 GB | < €1 |
| Monitoring | self-hosted / free tier | €0 |

Concurrency, not server count, drives cost. Assumption for planning: at any moment ~2-5% of servers
that use the bot are playing; 24/7 premium servers count as always-on.

## 3. Payment economics (Discord Premium Apps)

| Item | Assumption |
|---|---|
| Price | $3.99 / server / month (assumption; benchmark against competitors before launch) |
| Discord share | 15% on the first $1M (developer receives 85%) — verify current Monetization Terms |
| Payment processing | "less applicable processing fees"; assume ~5% |
| Net per subscription | ≈ $3.99 × 0.85 × 0.95 ≈ **$3.22 / month** |
| Payout threshold | first $100 earned, then review (official docs) |
| Taxes/VAT | Determine whether Discord acts as merchant of record for EU VAT; until confirmed, assume the developer must account for income tax and possibly VAT — **accountant** |

## 4. Break-even

| Scenario | Monthly cost | Subscriptions to break even |
|---|---|---|
| Today: 1 VPS (20 sessions), owner's time free | €10 | **4** |
| 1,000 servers, 5% concurrency = 50 sessions → 3 VPS | €30 | 10 |
| 5,000 servers, 4% → 200 sessions → 10 VPS + backups | €105 | 35 |
| Each 24/7 premium server permanently occupies a slot | €0.50 | 0.16 of its own fee |

Premium is profitable per customer by a wide margin (24/7 costs ≈ €0.50 against ≈ $3.22 net);
the risk is free-tier load. Levers: the soft session cap (free refused first), idle timeouts
(5 min free), and empty-channel auto-leave (2 min).

## 5. What to measure after launch

- `shz_voice_sessions`, `shz_playing_sessions` (peak concurrency → VPS count)
- CPU per session: `container CPU / shz_playing_sessions`
- `shz_premium_guilds` × price vs VPS spend (monthly)
- `shz_source_errors_total{kind="transient"}` (provider blocks → may require proxies, a real cost)
- Conversion: premium prompts shown (`shz_commands_total{outcome="premium"}`) vs new premium guilds
