# Top.gg listing

Everything needed for the bot's page on top.gg. Bot ID / application ID: `892731315928047637`.

| Top.gg field | What to put there |
|---|---|
| Short description | `Music for your voice channels: YouTube & SoundCloud, playlists, a one-click player panel, slash commands and cute anime GIFs.` |
| Long description | The whole of [`long-description.html`](long-description.html), including the `<style>` block at the top |
| Prefix | `/` (slash commands only) |
| Invite link | `https://discord.com/oauth2/authorize?client_id=892731315928047637&permissions=3230720&scope=bot+applications.commands` |
| Tags | Music (plus Fun / Utility if you want more) |
| Commands tab | [`commands.json`](commands.json) (see below) |
| Support server / website | Leave empty until you have a support server. Don't link the draft Terms/Privacy docs, they still have placeholders. |

## Long description

- Paste the file as is. It has **no blank lines** on purpose: top.gg runs the description through
  Markdown, and a blank line inside raw HTML can end the HTML block and show the rest as code.
  If you edit it, keep it that way (a test checks this).
- All CSS is scoped to `.shz` and only styles this description, never top.gg's own page, and there
  is no JavaScript.
- The banner loads from GitHub:
  `raw.githubusercontent.com/Moe-Ara/Shehrazad/commercial-readiness/assets/banner-1360x480.png`.
  If the `commercial-readiness` branch is deleted after merging, change it to `main` in the URL.
- The "Vote on top.gg" button works once the bot is approved.

## Widget

[`widget.html`](widget.html) has three options:

- **A: Shehrazad card (recommended).** The banner, avatar, English name, live top.gg server and vote
  badges, plus Add to Discord and Vote buttons. It uses inline styles only, so it works pasted into
  any website.
- **B: small badges only.** Servers, votes and owner. They don't show the bot name, so they look
  right today. The repo README uses these.
- **C: top.gg's own large widget.** It draws the name from top.gg's stored copy, which is still the old
  Arabic name, and top.gg's font can't render Arabic, so it shows boxes. Discord already has
  "Shehrazad" for both the bot and the application. Top.gg updates its copy on its own schedule,
  and I couldn't find a documented way to force it. If the dashboard still shows the Arabic name
  after a day or two, ask top.gg support to resync the bot from Discord.

**Server count:** widgets show 0 servers until the bot reports its count. Put the project token from
the top.gg dashboard (Integrations & API) in the VPS `.env` as `TOPGG_TOKEN=...` and restart. The bot
then posts its server count every 30 minutes (`PATCH /api/v1/projects/@me/metrics`).

## Commands (JSON)

[`commands.json`](commands.json) holds all 33 slash commands (with their subcommands and options) in
Discord's application command format. It is generated from the bot itself and is exactly what the bot
registers with Discord. That is the format top.gg's **Commands** tab and API take. To push it with
the API, use the project token from the top.gg dashboard:

```bash
curl -X PUT https://top.gg/api/v1/projects/@me/commands \
  -H "Authorization: Bearer $TOPGG_TOKEN" -H "Content-Type: application/json" \
  --data @docs/topgg/commands.json
```

Regenerate it after changing commands with `python scripts/export_commands.py`. A test fails if it's
out of date.
