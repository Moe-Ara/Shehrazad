import random
import discord
from discord import app_commands
from discord.ext import commands
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse, parse_qs
import yt_dlp as youtube_dl
from yt_dlp.utils import DownloadError
import asyncio
import Common

intents = discord.Intents.default()
intents.messages = True
try:
    intents.message_content = True
except AttributeError:
    pass

bot = commands.Bot(command_prefix='!', intents=intents)
bot_id = Common.bot_id
TOKEN = Common.TOKEN
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    # normalize/boost streamed audio so quieter tracks keep level with louder ones
    'options': '-vn -ar 48000 -ac 2 -b:a 192k -af "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000"'
}

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def pick_best_entry(entries, query):
    best = entries[0]
    best_score = -1.0
    normalized_query = query.lower()
    for entry in entries:
        title = entry.get('title') or ''
        artist = entry.get('artist') or entry.get('uploader') or ''
        combined = ' '.join(part for part in (title, artist) if part).lower()
        score = similarity(normalized_query, combined or title.lower())
        if score > best_score:
            best_score = score
            best = entry
    return best


def select_best_entry(info_dict, query=None):
    if not isinstance(info_dict, dict):
        return None
    entries = info_dict.get('entries')
    if entries:
        if query:
            return pick_best_entry(entries, query)
        return entries[0]
    return info_dict


ytdl_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'nocheckcertificate': True,
    'quiet': True,
    'ignoreerrors': True,
    'extractor_args': {
        'youtube': {
            # ios/android return pre-decrypted URLs — no JS or cookies needed.
            'player_client': ['ios', 'android'],
        },
    },
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# Flat extractor for playlists: list every entry's page URL fast, without
# resolving each track's stream up front (play_next resolves on demand).
ytdl_playlist_options = {
    **ytdl_format_options,
    'noplaylist': False,
    'extract_flat': 'in_playlist',
}
ytdl_playlist = youtube_dl.YoutubeDL(ytdl_playlist_options)

song_queue = []
is_playing = False
current_song = None
commands_synced = False

# --- playback state ---
playback_volume = 1.0              # 0.0 - 2.0, applied live via PCMVolumeTransformer
current_source = None              # active PCMVolumeTransformer
current_audio_url = None           # direct stream url of the playing track
current_before_base = None         # reconnect + header ffmpeg input options
current_duration = None            # track length in seconds (for the progress bar)
playback_started_at = None         # bot.loop.time() when current segment started (None = paused)
current_offset = 0.0               # seconds already elapsed before current segment


def reset_playback_state():
    global is_playing, current_song, current_source
    global current_audio_url, current_before_base, current_duration
    global playback_started_at, current_offset
    is_playing = False
    current_song = None
    current_source = None
    current_audio_url = None
    current_before_base = None
    current_duration = None
    playback_started_at = None
    current_offset = 0.0


def current_position():
    """Approximate seconds into the current track."""
    if playback_started_at is None:
        return current_offset
    return current_offset + (bot.loop.time() - playback_started_at)


def start_source(ctx):
    """Spawn ffmpeg for the current track and start playing it."""
    global current_source, playback_started_at, current_offset
    voice_client = get_voice_client(ctx)
    if voice_client is None or not voice_client.is_connected() or not current_audio_url:
        return False

    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(
            current_audio_url,
            before_options=current_before_base or '',
            options=FFMPEG_OPTIONS['options'],
        ),
        volume=playback_volume,
    )

    def after_playing(error):
        if error:
            print(f"player error: {error}")
        fut = asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"Error in after_playing: {e}")

    current_source = source
    current_offset = 0.0
    playback_started_at = bot.loop.time()
    voice_client.play(source, after=after_playing)
    return True


def set_volume(value):
    """Live volume change (no respawn needed)."""
    global playback_volume
    playback_volume = max(0.0, min(2.0, value))
    if current_source is not None:
        current_source.volume = playback_volume
    return playback_volume


def pause_playback(voice_client):
    global playback_started_at, current_offset
    if voice_client and voice_client.is_playing() and not voice_client.is_paused():
        current_offset = current_position()
        playback_started_at = None
        voice_client.pause()
        return True
    return False


def resume_playback(voice_client):
    global playback_started_at
    if voice_client and voice_client.is_paused():
        playback_started_at = bot.loop.time()
        voice_client.resume()
        return True
    return False


def _fmt_time(seconds):
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def build_progress(pos, duration):
    if duration and duration > 0:
        ratio = max(0.0, min(1.0, pos / duration))
        filled = int(ratio * 20)
        bar = '▬' * filled + '🔘' + '▬' * (19 - filled)
        return f"{bar}\n{_fmt_time(pos)} / {_fmt_time(duration)}"
    return _fmt_time(pos)


def build_now_playing_embed():
    embed = discord.Embed(title="عم يشتغل هلق", color=0x1DB954)
    embed.add_field(
        name="المقطع",
        value=current_song['title'] if current_song else "ما في شي عم يشتغل",
        inline=False,
    )
    if current_song:
        embed.add_field(name="الوقت", value=build_progress(current_position(), current_duration), inline=False)
    embed.add_field(name="الصوت", value=f"{int(playback_volume * 100)}%", inline=True)
    return embed


def get_voice_client(ctx):
    if isinstance(ctx, commands.Context):
        return ctx.voice_client
    if ctx.guild:
        return ctx.guild.voice_client
    return None


async def send_response(ctx, content, **kwargs):
    if isinstance(ctx, commands.Context):
        return await ctx.send(content, **kwargs)
    if isinstance(ctx, discord.Interaction):
        if not ctx.response.is_done():
            return await ctx.response.send_message(content, **kwargs)
        return await ctx.followup.send(content, **kwargs)
    channel = getattr(ctx, "channel", None)
    if channel:
        return await channel.send(content, **kwargs)


async def ensure_voice_connection(ctx, user):
    voice_client = get_voice_client(ctx)
    if voice_client and voice_client.is_connected():
        return voice_client

    if not user.voice or not user.voice.channel:
        await send_response(ctx, "لازم تكون موجود بالقناة الصوتية أولاً.")
        return None

    try:
        voice_client = await user.voice.channel.connect()
    except Exception as e:
        await send_response(ctx, "فشل الربط مع القناة الصوتية تبعتك.")
        await send_response(ctx, str(e))
        return None

    return voice_client


async def play_next(ctx):
    global is_playing, song_queue, current_song
    global current_audio_url, current_before_base, current_duration

    if not song_queue:
        reset_playback_state()
        return

    is_playing = True
    song = song_queue.pop(0)
    voice_channel = get_voice_client(ctx)
    if voice_channel is None or not voice_channel.is_connected():
        reset_playback_state()
        return

    source_url = song.get('source_url') or song.get('url')
    if not source_url:
        await send_response(ctx, "ما عرفت اسحب الغنية من الرابط")
        reset_playback_state()
        return

    try:
        with ytdl:
            info = ytdl.extract_info(source_url, download=False)
    except DownloadError as exc:
        await send_response(ctx, "ما قدرت نزل الغنية")
        await send_response(ctx, str(exc))
        is_playing = False
        current_song = None
        await play_next(ctx)
        return
    except Exception as exc:
        await send_response(ctx, "خطأ تنزيل")
        await send_response(ctx, str(exc))
        is_playing = False
        current_song = None
        await play_next(ctx)
        return

    info = select_best_entry(info)
    if not isinstance(info, dict) or 'url' not in info:
        await send_response(ctx, f"ما عرفت اسحب الغنية من الرابط: {song.get('title', '')}")
        is_playing = False
        current_song = None
        await play_next(ctx)
        return

    before_options = FFMPEG_OPTIONS['before_options']
    headers = dict(info.get('http_headers') or {})
    headers.setdefault('Referer', 'https://www.youtube.com/')
    headers.setdefault('Origin', 'https://www.youtube.com')
    if headers:
        header_lines = ''.join(f'{k}: {v}\r\n' for k, v in headers.items())
        header_lines = header_lines.replace('"', '\\"')
        before_options = f'{before_options} -headers "{header_lines}"'

    current_audio_url = info['url']
    current_before_base = before_options
    current_duration = info.get('duration') or song.get('duration')
    current_song = song

    if not start_source(ctx):
        reset_playback_state()
        return
    await send_response(ctx, f"هلا عم يشتغل: {song['title']}")


def is_spotify_url(url):
    return 'spotify.com' in url


def is_url(value):
    return bool(re.match(r'^(https?://|ftp://|www\.)', value, re.IGNORECASE))


def is_playlist_url(url):
    """True when the URL points at a collection of tracks, not a single one.

    A link to one specific song is always a single song, even when the site
    tacks playlist context onto it. YouTube adds `&list=...` to normal video
    links shared from a mix, radio or playlist, and SoundCloud adds
    `?in=artist/sets/...`; neither of those should queue the whole list.
    """
    if not is_url(url):
        return False
    parsed = urlparse(url if '://' in url else f'https://{url}')
    host = parsed.netloc.lower()
    path = parsed.path.lower().rstrip('/')
    params = parse_qs(parsed.query)

    if host.endswith('youtu.be'):
        return False  # youtu.be/<video id> is always one video
    if 'youtube.com' in host:
        if params.get('v') or path.startswith(('/shorts/', '/live/', '/embed/')):
            return False  # a specific video, even if &list= is attached
        if path == '/playlist' and params.get('list'):
            return True
        if host.startswith('music.') and path.startswith('/browse/'):
            return True  # YouTube Music album / playlist page
        return bool(params.get('list'))
    if 'soundcloud.com' in host:
        return '/sets/' in path
    if 'spotify.com' in host:
        return '/playlist/' in path or '/album/' in path
    return path.endswith('/playlist')


async def queue_song(ctx, user, url):
    voice_client = await ensure_voice_connection(ctx, user)
    if voice_client is None or not voice_client.is_connected():
        return

    target = url
    is_search = False
    search_query = None
    if not is_url(url):
        target = f"ytsearch:{url}"
        is_search = True
        search_query = url

    try:
        with ytdl:
            info = ytdl.extract_info(target, download=False)
    except DownloadError as exc:
        await send_response(ctx, "ما قدرت نزل الغنية")
        await send_response(ctx, str(exc))
        return
    except Exception as exc:
        await send_response(ctx, "خطأ تنزيل")
        await send_response(ctx, str(exc))
        return

    info = select_best_entry(info, search_query if is_search else None)
    if not info:
        await send_response(ctx, "ما لقيت الغنية المطلوبة.")
        return

    if is_spotify_url(url):
        title = info.get('title') or info.get('track') or ''
        artist = info.get('artist') or info.get('uploader') or ''
        query = ' '.join(p for p in [title, artist] if p).strip()
        if query:
            try:
                with ytdl:
                    search_info = ytdl.extract_info(f"ytsearch:{query}", download=False)
                info = select_best_entry(search_info, query)
            except DownloadError as exc:
                await send_response(ctx, "ما قدرت نزل الغنية من سبوتيفاي.")
                await send_response(ctx, str(exc))
                return
            except Exception as exc:
                await send_response(ctx, "خطأ تنزيل من سبوتيفاي.")
                await send_response(ctx, str(exc))
                return

    if not info or 'url' not in info:
        await send_response(ctx, "ما لقيت الغنية المطلوبة.")
        return

    song_queue.append({
        'source_url': info.get('webpage_url') or info.get('original_url') or info.get('url') or url,
        'title': info.get('title', 'Unknown Track'),
        'duration': info.get('duration'),
    })
    await send_response(ctx, f" :يتم تشغيل {info.get('title', 'Unknown Track')}")
    if not is_playing:
        await play_next(ctx)


async def queue_playlist(ctx, user, url, shuffle=False):
    voice_client = await ensure_voice_connection(ctx, user)
    if voice_client is None or not voice_client.is_connected():
        return

    try:
        with ytdl_playlist:
            info = ytdl_playlist.extract_info(url, download=False)
    except DownloadError as exc:
        await send_response(ctx, "ما قدرت أقرأ البلايليست.")
        await send_response(ctx, str(exc))
        return
    except Exception as exc:
        await send_response(ctx, "خطأ بقراءة البلايليست.")
        await send_response(ctx, str(exc))
        return

    entries = info.get('entries') if isinstance(info, dict) else None
    if not entries:
        # Not actually a playlist (or unsupported) — treat it as a single track.
        await queue_song(ctx, user, url)
        return

    new_songs = []
    for entry in entries:
        if not entry:
            continue
        source_url = entry.get('url') or entry.get('webpage_url') or entry.get('original_url')
        if not source_url:
            continue
        new_songs.append({
            'source_url': source_url,
            'title': entry.get('title', 'Unknown Track'),
            'duration': entry.get('duration'),
        })

    if not new_songs:
        await send_response(ctx, "البلايليست فاضية أو ما قدرت أقرأها.")
        return

    if shuffle:
        random.shuffle(new_songs)

    song_queue.extend(new_songs)
    playlist_title = info.get('title') or 'البلايليست'
    shuffle_note = " (مخلوطة 🔀)" if shuffle else ""
    await send_response(ctx, f"📃 ضفت {len(new_songs)} مقطع من «{playlist_title}»{shuffle_note}.")
    if not is_playing:
        await play_next(ctx)


async def enqueue(ctx, user, query, shuffle=False):
    """Route a play request to playlist or single-track handling."""
    if is_playlist_url(query):
        await queue_playlist(ctx, user, query, shuffle=shuffle)
    else:
        await queue_song(ctx, user, query)


def build_queue_embed():
    embed = discord.Embed(title="قائمة الأغاني", color=0x2F3136)
    now_value = current_song['title'] if current_song else "ما عم يشتغل شي"
    embed.add_field(name="عم يشتغل هلق", value=now_value, inline=False)

    if song_queue:
        queue_lines = []
        for idx, song in enumerate(song_queue[:10], start=1):
            queue_lines.append(f"{idx}. {song.get('title', 'مقطع بدون اسم')}")
        if len(song_queue) > 10:
            queue_lines.append(f"... +{len(song_queue) - 10} أكتر بالصف")
        embed.add_field(name="الصف بعدين", value="\n".join(queue_lines), inline=False)
    else:
        embed.add_field(name="الصف بعدين", value="فاضي حاليًا.", inline=False)

    embed.add_field(name="الصوت", value=f"{int(playback_volume * 100)}%", inline=True)
    return embed


class QueueControlView(discord.ui.View):
    def __init__(self, timeout=180):
        super().__init__(timeout=timeout)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message("هالأزرار بس للسيرفرات.", ephemeral=True)
            return False

        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            await interaction.response.send_message("أنا مش متصل لهلق.", ephemeral=True)
            return False

        if (
            interaction.user.voice is None
            or interaction.user.voice.channel != voice_client.channel
        ):
            await interaction.response.send_message(
                "لازم تكون بنفس القناة الصوتية.", ephemeral=True
            )
            return False

        return True

    async def refresh_embed(self, interaction: discord.Interaction):
        if interaction.message:
            try:
                await interaction.message.edit(embed=build_queue_embed(), view=self)
            except Exception:
                pass

    @discord.ui.button(label="تخطي", style=discord.ButtonStyle.primary, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            await interaction.response.send_message("تخطيت المقطع.", ephemeral=True)
        else:
            await interaction.response.send_message("ما في شي عم يشتغل.", ephemeral=True)
        await self.refresh_embed(interaction)

    @discord.ui.button(label="إيقاف مؤقت", style=discord.ButtonStyle.secondary, emoji="⏸️")
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = interaction.guild.voice_client
        if pause_playback(voice_client):
            await interaction.response.send_message("وقفت الصوت مؤقتًا.", ephemeral=True)
        else:
            await interaction.response.send_message("ما في شي يشتغل أو الصوت متوقف.", ephemeral=True)
        await self.refresh_embed(interaction)

    @discord.ui.button(label="استمرار", style=discord.ButtonStyle.success, emoji="▶️")
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = interaction.guild.voice_client
        if resume_playback(voice_client):
            await interaction.response.send_message("رجّعت الصوت يشتغل.", ephemeral=True)
        else:
            await interaction.response.send_message("ما في شي متوقف لتشغيله.", ephemeral=True)
        await self.refresh_embed(interaction)

    @discord.ui.button(label="وقف", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        global is_playing, current_song, song_queue
        voice_client = interaction.guild.voice_client
        if voice_client:
            voice_client.stop()
            await voice_client.disconnect()

        is_playing = False
        current_song = None
        song_queue.clear()
        reset_playback_state()
        await interaction.response.send_message("طلّقنا الصوت وفضّيت الصف.", ephemeral=True)
        await self.refresh_embed(interaction)

    @discord.ui.button(label="الصوت -", style=discord.ButtonStyle.secondary, emoji="🔉", row=1)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        level = set_volume(playback_volume - 0.1)
        await interaction.response.send_message(f"🔉 الصوت: {int(level * 100)}%", ephemeral=True)
        await self.refresh_embed(interaction)

    @discord.ui.button(label="الصوت +", style=discord.ButtonStyle.secondary, emoji="🔊", row=1)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        level = set_volume(playback_volume + 0.1)
        await interaction.response.send_message(f"🔊 الصوت: {int(level * 100)}%", ephemeral=True)
        await self.refresh_embed(interaction)

    @discord.ui.button(label="عم يشتغل", style=discord.ButtonStyle.success, emoji="🎶", row=1)
    async def now_playing(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_now_playing_embed(), ephemeral=True)

@bot.command(aliases=['j', 'connect','t3e'])
async def join(ctx):
    response = random.choice(Common.coming_to_vc_responses)
    if not ctx.message.author.voice:
        await ctx.send("حبيب انت مانك قاعد بغرفة صوت")
        return
    channel = ctx.message.author.voice.channel
    await ctx.send(response)
    try:
        await channel.connect()
    except Exception as e:
        await ctx.send("همممممممممممم")
        await ctx.send(str(e))

@bot.command(aliases=['shosho','bae'])
async def babe(ctx):
    if ctx.author.id==410821336072716288:
        await ctx.send("شو تشكل اسي")
    if ctx.message.author.id==492867665551949824:
        await ctx.send("كول خرا حارث")


@bot.command(aliases=['p', 'l3be'])
async def play(ctx, *, query: str):
    await enqueue(ctx, ctx.author, query)


@bot.tree.command(name="play")
@app_commands.describe(query="URL or search terms for the song (playlist URLs queue every track)")
async def slash_play(interaction: discord.Interaction, query: str):
    await interaction.response.defer(thinking=True)
    await enqueue(interaction, interaction.user, query)


@bot.command(name='playlist', aliases=['pl'])
async def playlist_cmd(ctx, *, url: str):
    await queue_playlist(ctx, ctx.author, url)


@bot.command(name='shuffleplay', aliases=['sp'])
async def shuffle_playlist(ctx, *, url: str):
    await queue_playlist(ctx, ctx.author, url, shuffle=True)


@bot.tree.command(name="playlist")
@app_commands.describe(
    url="URL of a YouTube/SoundCloud playlist or album",
    shuffle="اخلط ترتيب المقاطع قبل ما تضيفهن",
)
async def slash_playlist(interaction: discord.Interaction, url: str, shuffle: bool = False):
    await interaction.response.defer(thinking=True)
    await queue_playlist(interaction, interaction.user, url, shuffle=shuffle)


@bot.tree.command(name="queue")
async def slash_queue(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("هالأمر مخصوص للسيرفرات.", ephemeral=True)
        return

    embed = build_queue_embed()
    view = QueueControlView()
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="nowplaying")
async def slash_nowplaying(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_now_playing_embed())


@bot.tree.command(name="volume")
@app_commands.describe(percent="مستوى الصوت من 0 إلى 200")
async def slash_volume(interaction: discord.Interaction, percent: int):
    level = set_volume(percent / 100)
    await interaction.response.send_message(f"🔊 الصوت: {int(level * 100)}%")


@bot.command(aliases=['np'])
async def nowplaying(ctx):
    await ctx.send(embed=build_now_playing_embed())


@bot.command(aliases=['vol', 'v'])
async def volume(ctx, percent: int):
    level = set_volume(percent / 100)
    await ctx.send(f"🔊 الصوت: {int(level * 100)}%")

@bot.command(aliases=['s'])
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()


@bot.command(aliases=['l','disconnect','ro7e'])
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client and voice_client.is_connected():
        response = random.choice(Common.disconnecting_from_vc_responses)
        await ctx.send(response)
        await voice_client.disconnect()
    else:
        await ctx.send("حبيب انت مانك قاعد بغرفة صوت")


@bot.event
async def on_ready():
    global commands_synced
    if not commands_synced:
        await bot.tree.sync()
        for guild in bot.guilds:
            try:
                await bot.tree.sync(guild=guild)
            except Exception as exc:
                print(f"failed to sync {guild}: {exc}")
        commands_synced = True
    print(f"Logged in as {bot.user}.")

if __name__ == '__main__':
    bot.run(TOKEN)
