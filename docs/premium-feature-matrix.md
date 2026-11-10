# Premium feature matrix

Single source of truth in code: `musicbot/premium.py` (`LIMITS`, `Feature`, `PREMIUM_FEATURES`).
Premium is **per server** (Discord guild subscription). It applies to everyone in that server and
to nothing outside it.

## Tiers

| Capability | Free | Premium server | Gate behaviour |
|---|---|---|---|
| Play by search, link, playlist link or uploaded file | ✅ | ✅ | — |
| Pause / resume / skip / previous / seek / volume / repeat | ✅ | ✅ | — |
| Queue view, remove, move, jump, shuffle, clear | ✅ | ✅ | — |
| Vote-skip, DJ role, DJ mode, request permissions | ✅ | ✅ | — |
| Persistent Now Playing panel | ✅ | ✅ | — |
| Favorites (per user) | 50 | 50 | Limit message |
| Recently played picker | 25 | 25 | — |
| Queue size | 200 | 2,000 | Free: "queue full" + one-line upsell |
| Playlist import per command | 100 tracks | 1,000 tracks | Free: truncated + one-line upsell |
| Idle timeout | default 5 min, max 10 | default 15 min, max 60 | Free asking >10 min → premium prompt |
| Saved server playlists (`/library save/load`) | — | 50 × 500 tracks | Premium prompt with upgrade button |
| 24/7 mode (`/settings stay-connected`) | — | ✅ (rejoins after restarts) | Premium prompt |
| Queue restore after restart/disconnect (`/restore`) | — | ✅ | Premium prompt |
| Priority capacity when busy | Refused above `MAX_SESSIONS_SOFT` | Allowed up to `MAX_SESSIONS_HARD` | "We're at capacity" |

## How gating works

1. Every check calls `EntitlementService.status(guild_id)` — server-side, never client state.
2. Status is premium when the guild has an **active Discord entitlement** for a configured SKU
   (`PREMIUM_SKU_IDS`; not deleted, not consumed, `starts_at <= now < ends_at` or no end)
   **or** an active operator grant.
3. Entitlements arrive through gateway events, the `entitlements` field of every interaction, and a
   reconciliation against the List Entitlements API every 30 minutes. Duplicates are ignored;
   refunds (delete) are terminal; a stale event can never extend or resurrect access; a failed
   reconciliation changes nothing.
4. Changes apply immediately (no restart). Grants made from the CLI are picked up within 60 s.
5. Upsells appear only (a) on `/premium` and (b) when a user hits a gate — never mid-playback,
   never as unsolicited messages. The premium button is shown only when `PREMIUM_SKU_IDS` is set,
   the `premium.sales` flag is on, and the server is not already premium.
6. Kill switches (`feature.saved_playlists`, `feature.stay_connected`) disable a feature for
   everyone, paid or not, with a "temporarily unavailable" message.

## Lifecycle handling

| Event | Effect |
|---|---|
| Purchase (`ENTITLEMENT_CREATE`) | Premium immediately |
| Renewal | No entitlement change (`ends_at` stays null) |
| Cancellation | No event; premium continues until the period ends |
| Subscription ends (`ENTITLEMENT_UPDATE` with `ends_at`) | Premium until `ends_at`, then free |
| Refund / removal (`ENTITLEMENT_DELETE`) | Free immediately; later stale events ignored |
| Bot offline during any of the above | Next reconciliation corrects state |
| Downgrade to free | Saved playlists are kept but read-only (list/view/delete work, save/load gated); 24/7 stops at next idle |

## Owner / lifetime premium (no payment)

See `docs/operator-guide.md` → "Premium for your own servers". Two supported routes, both audited
or official — no hard-coded IDs:

1. **Now:** `python -m musicbot.cli premium grant <guild_id> --reason "owner"` (never expires).
2. **After monetization is enabled:** a Discord test entitlement for your guild (valid in perpetuity)
   or a team-member purchase (Discord gives team members a 100% discount).
