import random
import discord
from discord import app_commands
from discord.ext import commands
import re
from difflib import SequenceMatcher
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
    'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '320',
    }],
    'noplaylist': 'True',
    'nocheckcertificate': 'True',
    'quiet': 'True',
    'ignoreerrors': 'True',
    'cookiefile': 'cookies.txt',
    'age-limit': 99,
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

song_queue = []
is_playing = False
current_song = None
commands_synced = False


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

    if not song_queue:
        is_playing = False
        current_song = None
        return

    is_playing = True
    song = song_queue.pop(0)
    voice_channel = get_voice_client(ctx)
    if voice_channel is None or not voice_channel.is_connected():
        is_playing = False
        current_song = None
        return

    with ytdl:
        info = ytdl.extract_info(song['url'], download=False)
        if 'url' not in info:
            await send_response(ctx, "ما عرفت اسحب الغنية من الرابط")
            is_playing = False
            current_song = None
            return

        audio_url = info['url']

    def after_playing(error):
        fut = asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"Error in after_playing: {e}")

    voice_channel.play(discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS), after=after_playing)
    current_song = song
    await send_response(ctx, f"هلا عم يشتغل: {song['title']}")


def is_spotify_url(url):
    return 'spotify.com' in url


def is_url(value):
    return bool(re.match(r'^(https?://|ftp://|www\.)', value, re.IGNORECASE))


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

    song_queue.append({'url': info['url'], 'title': info.get('title', 'Unknown Track')})
    await send_response(ctx, f" :يتم تشغيل {info.get('title', 'Unknown Track')}")
    if not is_playing:
        await play_next(ctx)

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
        if voice_client and voice_client.is_playing() and not voice_client.is_paused():
            voice_client.pause()
            await interaction.response.send_message("وقفت الصوت مؤقتًا.", ephemeral=True)
        else:
            await interaction.response.send_message("ما في شي يشتغل أو الصوت متوقف.", ephemeral=True)
        await self.refresh_embed(interaction)

    @discord.ui.button(label="استمرار", style=discord.ButtonStyle.success, emoji="▶️")
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_paused():
            voice_client.resume()
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
        await interaction.response.send_message("طلّقنا الصوت وفضّيت الصف.", ephemeral=True)
        await self.refresh_embed(interaction)

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
    await queue_song(ctx, ctx.author, query)


@bot.tree.command(name="play")
@app_commands.describe(query="URL or search terms for the song")
async def slash_play(interaction: discord.Interaction, query: str):
    await interaction.response.defer(thinking=True)
    await queue_song(interaction, interaction.user, query)


@bot.tree.command(name="queue")
async def slash_queue(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("هالأمر مخصوص للسيرفرات.", ephemeral=True)
        return

    embed = build_queue_embed()
    view = QueueControlView()
    await interaction.response.send_message(embed=embed, view=view)

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
