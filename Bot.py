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

        await ctx.send(f"Now playing: {song['title']}")
    else:
        is_playing = False


@bot.command()
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send("You are not in a voice channel!")
        return
    channel = ctx.message.author.voice.channel
    await channel.connect()


@bot.command()
async def play(ctx, url):
    global is_playing
    if ctx.voice_client is None:
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            await channel.connect()
        else:
            await ctx.send("You need to be in a voice channel to play music.")
            return

    if not ctx.voice_client.is_connected():
        await ctx.send("I'm not connected to a voice channel!")
        return
    try:
        with ytdl:
            info = ytdl.extract_info(url, download=False)
            if info is None:
                await ctx.send("Error: Couldn't retrieve information from the provided URL.")
                return
    except Exception as e:
        await ctx.send(f"An error occurred while retrieving the song info: {str(e)}")
        return

    song = {'url': url, 'title': info['title']}

    song_queue.append(song)
    await ctx.send(f"Added {info['title']} to the queue.")

    await play_next(ctx)


@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()


@bot.command()
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send("I'm not in a voice channel!")


if __name__ == '__main__':
    bot.run(TOKEN)
