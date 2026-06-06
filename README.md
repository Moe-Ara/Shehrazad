
## Discord Music Bot

This bot allows users to stream music from YouTube to a Discord voice channel using `yt-dlp` for YouTube link processing and `discord.py` for bot interactions. It supports basic commands for joining/leaving voice channels, playing/pausing music, and skipping tracks.

Just to let you know, Shehrazad replies in Arabic. It is functional, and anyone can use it since the commands are in English. Enjoy

### Features

- **Join Voice Channel**: The bot joins the user's current voice channel.
- **Play Music**: Streams music from a provided YouTube URL.
- **Queue System**: Automatically queues songs.
- **Playlists**: Queues every track from a YouTube/SoundCloud playlist or album in one command (with optional shuffle).
- **Skip Track**: Skips the current playing track.
- **Leave Channel**: Disconnects the bot from the voice channel.

### Prerequisites

Ensure you have Python installed and the following packages:

- `discord.py`
- `yt-dlp`
- `ffmpeg`

To install the necessary packages:

```bash
pip install discord.py yt-dlp
```

You also need `FFmpeg` installed. Follow the instructions for your OS [here](https://ffmpeg.org/download.html).

### Setup

1. Create a `Common.py` file with the following variables:

```python
# Common.py
bot_id = 'YOUR_BOT_ID'
TOKEN = 'YOUR_DISCORD_BOT_TOKEN'
coming_to_vc_responses = ["response1", "response2", ...] # Add your responses here
disconnecting_from_vc_responses = ["response1", "response2", ...] # Add your responses here
```

2. Add your bot token and relevant responses in the `Common.py` file.

3. Run the bot:

```bash
python bot.py
```

### Usage

Commands:

- **`!join` / `!j` / `!connect` / `!t3e`**: The bot joins your voice channel.
- **`!play [URL or search terms]` / `!p` / `!l3be`**: Plays a song. If the URL is a playlist, every track is queued automatically.
- **`!playlist [playlist URL]` / `!pl`**: Queues every track from a YouTube/SoundCloud playlist or album.
- **`!shuffleplay [playlist URL]` / `!sp`**: Same as `!playlist`, but shuffles the tracks before queueing.
- **`!skip` / `!s`**: Skips the current song.
- **`!leave` / `!l` / `!disconnect` / `!ro7e`**: The bot leaves the voice channel.

Slash commands are also available: `/play`, `/playlist` (with a `shuffle` option), `/queue`, `/nowplaying`, and the audio-effect commands.
