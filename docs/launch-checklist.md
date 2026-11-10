# Launch checklist

Legend: `[x]` done and verified by automated tests/checks · `[~]` implemented, **not yet verified
live on Discord** · `[ ]` open · `[!]` blocked on an owner/external action.

## Product & UX
- [x] Slash commands for playback, queue, library, settings, info (48 commands in 32 top-level entries; limit 100; payloads validated)
- [~] Persistent Now Playing panel edits in place, rate-limited, re-posted when buried
- [x] Stale panel buttons rejected and disabled
- [~] `/setup` wizard with channel/role pickers and permission diagnostics
- [x] Consistent embeds, colours, icons, text labels on all buttons
- [x] Errors translated, no stack traces/secrets; `ref` correlation ID shown
- [x] English only (one catalog; a test fails if Arabic text appears in the code)
- [~] Autocomplete on `/play` (favorites + server history) and `/library load|view|delete`
- [ ] Screenshots and App Directory listing (`docs/app-directory.md` has the copy)

## Playback reliability
- [x] Per-guild sessions; multiple guilds play independently (tests)
- [x] No blocking extraction on the event loop (test proves loop keeps running)
- [x] Race-free transitions under concurrent skip/remove/enqueue (test)
- [x] Unavailable tracks skipped with notice; transient errors retried; outage pauses queue
- [x] Idle timeout, empty-channel pause/leave, external disconnect cleanup
- [~] Live playback on the new build (voice + DAVE verified on v1 only)
- [~] 24/7 rejoin after restart (premium)
- [ ] Load test with ≥20 concurrent sessions on the target VPS

## Premium & billing
- [x] Entitlement service: create/update/delete, duplicates, out-of-order, expiry, refunds, reconcile, API failure (tests)
- [x] Guild-scoped gates; user/other-SKU entitlements don't leak (tests)
- [x] Premium button only when SKU configured, sales flag on, guild not premium
- [x] Audited comp grants via CLI; no hard-coded premium users
- [!] Transfer app to a developer team; verify the app; team owner 2FA
- [!] Accept Monetization Terms and Developer Policy; set up Stripe payouts
- [!] Create the guild subscription SKU; set `PREMIUM_SKU_IDS`
- [ ] Test a purchase with a team member (100% discount) and a test entitlement on staging
- [ ] Re-read Developer Policy/Monetization Policy in the official text (automated fetch was blocked)

## Security & privacy
- [x] Token only from env/properties, never logged (redaction tests)
- [x] SSRF: generic extractor disabled; direct URLs must resolve to public IPs
- [x] Minimal invite permissions; minimal intents (no privileged intents; slash commands only)
- [x] Per-user rate limits on commands, buttons, feedback, privacy actions
- [x] Data export/delete (user and server), retention purge, deletion 30 days after removal
- [ ] Rotate the Discord bot token and the VPS root password (both were shared in chat)
- [ ] Remove `cookies.txt` from git history (contains old YouTube cookies)
- [ ] SSH key auth on the VPS, disable password login
- [x] ToS and Privacy Policy hosted on GitHub Pages (https://moe-ara.github.io/Shehrazad/terms.html, https://moe-ara.github.io/Shehrazad/privacy.html)
- [ ] Add your legal name and a postal contact (EU controller/Impressum) before selling; legal review
- [!] Music licensing decision (deferred by owner — see readiness report §5)
- [x] `!babe` easter egg removed with all prefix commands (harmful-language rule)
- [ ] Turn off the Message Content intent in the Developer Portal (Bot → Privileged Gateway Intents); the bot no longer uses it

## Operations
- [x] Health/readiness/metrics endpoints (tests)
- [x] Graceful shutdown; non-root container; healthcheck; log rotation
- [x] Kill switches without restart
- [x] Docker image builds on the VPS and runs as uid 10001 (`shehrazad:2.0.0`)
- [ ] Staging Discord application + `DEV_GUILD_ID`
- [ ] Off-box backups scheduled; restore rehearsed
- [ ] Alerts wired (see operator guide)
- [x] Deploy v2 to production (2026-11-10: ready in 6 s, 32 commands synced, 3 servers, premium grants active)

## Business
- [!] Business identity/registration for receiving payouts (Germany: Gewerbe, tax number)
- [!] VAT handling: confirm whether Discord is merchant of record for EU sales (accountant)
- [!] EU consumer rules for subscriptions (withdrawal right, cancellation) — legal review
- [ ] Support server, `SUPPORT_URL`, `FEEDBACK_CHANNEL_ID`
- [ ] Pricing decision (cost model assumes $3.99/server/month)
