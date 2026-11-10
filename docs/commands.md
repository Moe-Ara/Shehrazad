# Command reference

All commands are slash commands and only work in servers. Replies that only matter to you are
private (ephemeral). Controls require being in the bot's voice channel.

## Playback
| Command | What it does | Who |
|---|---|---|
| `/play query:<name or link> [file] [next] [shuffle]` | Play a song, YouTube/SoundCloud link, playlist link or uploaded audio file. Autocompletes from your favorites and this server's history. Song links that carry `&list=` play just that song. | Everyone (or DJs if requests are DJ-only) |
| `/playlist url [shuffle]` | Queue a whole playlist or album, even from a `watch?v=…&list=…` link | Same as `/play` |
| `/pause`, `/resume` | Pause / resume | Controllers¹ |
| `/skip` | Skip; non-DJs start a vote when DJ mode is on | Everyone in channel |
| `/previous` | Back to the previous track | Controllers |
| `/seek time:1:30` | Jump within the track (`1:30`, `90`, `1m30s`) | Controllers |
| `/volume [level]` | Show or set volume (1–150%) | Controllers to change |
| `/loop [mode]` | Repeat off / track / queue (empty = cycle) | Controllers |
| `/nowplaying` | Current track, with a link to the player panel | Everyone |
| `/stop` / `/leave` | Stop, clear the queue and leave | Controllers |
| `/join` | Join your voice channel | Everyone |
| `/restore` | Restore the last saved queue ✨ | Requesters |

## Queue
| Command | What it does |
|---|---|
| `/queue [page]` | Paginated queue with a "jump to" menu |
| `/remove position` | Remove a track (your own tracks are always removable) |
| `/move source target` | Reorder |
| `/jump position` | Skip straight to a queued track |
| `/shuffle` | Shuffle upcoming tracks |
| `/clear` | Clear upcoming tracks |
| `/history` | Recently played here; pick tracks to queue again |

## Library
| Command | What it does |
|---|---|
| `/library save name` | Save current + upcoming tracks as a server playlist ✨ |
| `/library load name [shuffle]` | Queue a saved playlist ✨ |
| `/library list`, `/library view name`, `/library delete name` | Manage saved playlists (creator or DJ can delete) |
| `/library like` or ❤️ on the panel | Add the current track to your favorites |
| `/library favorites` | Pick favorites to queue |

## Server setup (Manage Server)
| Command | What it does |
|---|---|
| `/setup` | Guided setup: player channel, DJ role, DJ mode, vote-skip, permission check |
| `/settings view` | All settings |
| `/settings music-channel`, `dj-role`, `dj-mode`, `requests`, `vote-skip`, `default-volume`, `idle-timeout`, `stay-connected` ✨, `reset` | Individual settings |

## Fun
| Command | What it does |
|---|---|
| `/anime picture [kind]` | Random cute anime picture: Catgirl, Foxgirl, Waifu or Husbando (credits the artist) |
| `/anime react <action> [user]` | Reaction GIF: hug, head pat, cuddle, wave, smile, high five, hold hands, dance and more |

Images come from [nekos.best](https://nekos.best), a fully SFW anime API, and only wholesome categories are used
(no violent or romantic reactions). Kill switch: `flag set feature.anime off`. Admins can hide `/anime` in
Server Settings → Integrations.

## Owner-only novelty commands
`/heybabe`, `/whereareyou`, `/comehere`, `/goaway`. They are registered only in the servers listed in
`NOVELTY_GUILD_IDS` (never globally, so they're not on top.gg or in other servers) and answer only `OWNER_IDS`;
anyone else gets a private "just for my owner" reply.

## Info
`/help`, `/premium`, `/status` (latency, sources, this server's player, permission problems),
`/invite`, `/support`, `/privacy` (export/delete your data or the server's), `/feedback`, `/about`.

## Player panel buttons
Back · Pause/Resume · Skip · Stop · Repeat · Shuffle · Vol − · Vol + · Queue · Like.
Buttons on an old panel are rejected with a pointer to `/nowplaying` and then disabled.

## Slash commands only
The bot answers slash commands and its own buttons, nothing else. There are no prefix (`!`)
commands and it does not request the Message Content intent. What the old `!` commands did:
`!playnext` → `/play next:True`, `!shuffleplay` → `/playlist shuffle:True`, `!like` → `/library like`;
every other `!name` is `/name`.

¹ Controllers: everyone, unless DJ mode is on — then DJs (DJ role or Manage Server) and the person
who requested the current track.
