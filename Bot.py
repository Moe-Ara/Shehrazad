import random

import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio

import Common

intents = discord.Intents.default()
intents.messages = True
intents.message_content=True

# Setup bot
bot = commands.Bot(command_prefix='!', intents=intents)
bot_id = Common.bot_id
TOKEN = Common.TOKEN
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -ar 48000 -ac 2 -b:a 192k',
}

ytdl_format_options = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'noplaylist': 'True',
    'extract_flat': 'True',  # Prevent full metadata extraction, to minimize errors
    'nocheckcertificate': 'True',
    'quiet': 'True',
    'ignoreerrors': 'True'  # Continue even if some metadata can't be extracted
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

song_queue = []
is_playing = False


async def play_next(ctx):
    global is_playing, song_queue

    if len(song_queue) > 0:
        is_playing = True
        song = song_queue.pop(0)
        voice_channel = ctx.voice_client

        with ytdl:
            info = ytdl.extract_info(song['url'], download=False)
            audio_url = info['url']

        voice_channel.play(discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS),
                           after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))

        await ctx.send(f"{song['title']}" + " هلق عمبلعب ")
    else:
        is_playing = False


@bot.command(aliases=['j', 'connect','t3e'])
async def join(ctx):
    response=Common.coming_to_vc_responses[random.randint(0, len(Common.coming_to_vc_responses))]
    if not ctx.message.author.voice:
        await ctx.send("حبيب انت مانك قاعد بغرفة صوت")
        return
    if ctx.author.id == 410821336072716288:
        await ctx.send(f"جايتك تشكل آسي")
    channel = ctx.message.author.voice.channel
    await ctx.send(response)
    try:
        await channel.connect()
    except Exception as e:
        await ctx.send("همممممممممممم")
        await ctx.send(str(e))



@bot.command(aliases=['p', 'l3be'])
async def play(ctx, url):
    global is_playing
    if ctx.voice_client is None:
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            await channel.connect()
        else:
            await ctx.send("حبيب انت مانك قاعد بغرفة صوت")
            return

    if not ctx.voice_client.is_connected():
        await ctx.send("ماني هونو احكي مع المهندز")
        return
    try:
        with ytdl:
            info = ytdl.extract_info(url, download=False)
            if info is None:
                await ctx.send("شو عالرابط الخرا؟")
                return
    except Exception as e:
        await ctx.send(" والله ترا في مشكلة يا حبيب القلب ")
        await ctx.send(f"{str(e)}")
        return

    song = {'url': url, 'title': info['title']}

    song_queue.append(song)
    if ctx.author.id == 410821336072716288:
        await ctx.send(f"{info['title']}" + " اي من عيوني بلعلبك ")
    else:
        await ctx.send(f"{info['title']}" + " رح العبلكن ")
    if not is_playing:
        await play_next(ctx)


@bot.command(aliases=['s'])
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()


@bot.command(aliases=['l','disconnect','ro7e'])
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        response=Common.disconnecting_from_vc_responses[random.randint(0, len(Common.disconnecting_from_vc_responses))]
        await ctx.send(response)
        await voice_client.disconnect()
    else:
        await ctx.send("حبيب انت مانك قاعد بغرفة صوت")


if __name__ == '__main__':
    bot.run(TOKEN)
