# Privacy Policy — DRAFT

> **Draft for legal review.** Written to match what the code actually does (`musicbot/store.py`,
> `musicbot/migrations/0001_initial.sql`). Fill in the bracketed parts and have it reviewed
> (GDPR: controller identity, legal bases, DPO if required) before publishing.

**Controller:** [Name / business name, address, contact e-mail]
**Last updated:** [date]

## What we collect
| Data | Why | Retention |
|---|---|---|
| Server ID and server settings (player channel, DJ role, toggles) | To run the bot the way the server configured it | Until the server deletes it, or 30 days after the bot is removed |
| Play history: track title/link, time, ID of the user who requested it | "Recently played", autocomplete | 30 days |
| Favorites: track title/link per user ID | Your liked tracks | Until you delete them |
| Saved server playlists: tracks and creator's user ID | Premium playlists | Until deleted (by the server or the creator); 30 days after removal |
| Queue snapshots for premium servers | Restore after restarts | Overwritten continuously; deleted on stop |
| Feedback you submit, with your user ID and server ID | Support and bug fixing | 90 days |
| Subscription status (Discord entitlement IDs, SKU, dates) | To unlock premium | While relevant to the subscription |
| Technical logs (server ID, command name, error type, correlation ID) | Reliability and security | [log rotation: ~5 × 10 MB] |

We **do not** collect message content (the bot has no access to it), voice audio, e-mail
addresses, payment details (handled by Discord), or profile information beyond user IDs.

## Legal bases (GDPR)
Performance of the service you requested (Art. 6(1)(b)); legitimate interests in security and
abuse prevention (Art. 6(1)(f)). [Confirm with counsel.]

## Sharing
Data is not sold. Processors: our hosting provider [name, location]. Discord processes payments.
Music is fetched from third-party platforms (e.g. YouTube) using the track query or link; no user
data is sent to them. `/anime` images are fetched from nekos.best with only the picture category; no
user data is sent.

## Your rights
Use `/privacy` to export or delete your data at any time (server admins can export/delete server
data). You can also contact [e-mail]. You may lodge a complaint with your supervisory authority.

## Changes
We will announce material changes in our support server [link].
