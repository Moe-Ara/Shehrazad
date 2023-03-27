import io
import os.path
import random
from asyncio import wait
import aiohttp
import Bot
import Common
import discord
import asyncio
import threading
import DownloadSongs
import yt_dlp
import python_weather



bot_id = Common.bot_id


class Command:
    def __init__(self, cmdText, aliases, arguments, function):
        self.CommandText = cmdText.lower()
        self.aliases = [cmdText] + aliases
        self.CommandArguments = arguments
        self.function = function

    def execute(self, args):
        return self.function(args)


##FUNCTIONS
def is_connected(ctx):
    voice_client = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
    return voice_client and voice_client.is_connected()


async def leaveCommand(ctx):
    if is_connected(ctx):
        for x in ctx.author.voice.channel.members:
            if x.id == bot_id:
                await ctx.voice_client.disconnect()
                Common.channel = None
                Common.connected_to_voice_chat = False
                return
        await ctx.send("حبيب انت مانك قاعد بغرفة صوت")

    else:
        await ctx.send("فك عن ايري")


async def joinCommand(ctx):
    # ctx -context (information about how the command was executed)
    # !info
    try:
        channel = ctx.author.voice.channel
        Common.connected_to_voice_chat = True
    except:
        await ctx.send("حبيب انت مانك قاعد بغرفة صوت")
        return

    try:
        Common.channel = await channel.connect()
        response = Common.coming_to_vc_responses[random.randint(0, len(Common.coming_to_vc_responses))]
        if ctx.author.id == 410821336072716288:
            await ctx.send(f"جايتك تشكل آسي")
        else:
            await ctx.send(str(response))
    except Exception as e:
        print(str(e))
        await ctx.send("شحني هون")


async def playCommand(links):
    if not Common.connected_to_voice_chat:
        await joinCommand(Common.context)
    # name = await DownloadSongs.downloadYoutube(links=links[0])
    ydl_opts = {'format': 'bestaudio/best',
                # 'postprocessors': [{
                #     'key': 'FFmpegExtractAudio',
                #     'preferredquality': '192',
                # }]
                }
    ##! Downloading song
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        song_info = ydl.extract_info(links[0], download=False)
    # allSongs = []
    # if not allSongs.__contains__(song_info):
    #     allSongs.append(song_info)
    await Common.context.send(f"Added {song_info['title']} to the queue")
    connection = Common.channel
    # if len(allSongs) == 0:
    #     print("ما في اغاني")
    while connection.is_playing():
        await asyncio.sleep(5)
    # connection.play(
    #     discord.FFmpegPCMAudio(executable="C:/ffmpeg/bin/ffmpeg.exe",
    #                            source=os.path.dirname(os.path.realpath(__file__)) + '\\songs\\' + str(allSongs[0])))

    connection.play(discord.FFmpegPCMAudio(song_info['url'],**Common.ffmpeg_options))
    # allSongs.pop(0)
    await Common.context.send(f"Playing {song_info['title']}")
    while connection.is_playing():
        await asyncio.sleep(15)
    connection.stop()


async def skipCommand(void):
    if Common.channel.is_playing():
        Common.channel.stop()


async def sendHentaiCommand(void):
    num= str(random.randint(1,45))
    Attributes=['uncensured', 'bitches']
    randomAttribute=Attributes[random.randint(0,1)]
    middle='-1-' if randomAttribute=='bitches' else '-' if randomAttribute=='uncensured' else ''
    async with aiohttp.ClientSession() as session:
        async with session.get(f'https://hentanime.net/wp-content/uploads/2020/08/hentai-'+randomAttribute+middle+num+'.jpg') as resp:
            if resp.status != 200:
                return await Common.context.send('try again')
            data = io.BytesIO(await resp.read())
            await Common.context.send(file=discord.File(data, 'hentai.png'))

async def getWeather(city):
    if city=="":
        await Common.context.send("بأي مدينة ؟")
        return
    async with python_weather.Client(format=python_weather.METRIC) as weather_client:
        weather= await weather_client.get(city)
        await Common.context.send(f"Temperature in {city} Today is : {weather.current.temperature} C")
        for forcasts in weather.forecasts:
            await Common.context.send(f"Temperature in {city} on {forcasts.date} : {forcasts.highest_temperature} C <-> {forcasts.lowest_temperature} C")


###COMMANDS
join = Command('join', ['j', 'connect'], [], joinCommand)
leave = Command('leave', ['l', 'disconnect'], [], leaveCommand)
play = Command('play', ['p', 'l3be'], [], playCommand)
skip = Command('skip', ['s'], [], skipCommand)
hentai= Command('hentai', ['h','sexme','sendnudes','sn'], [], sendHentaiCommand)
weather= Command('weather', ['getweather','temp', 'w', 'forcast'],[],getWeather)
# COMMAND LIST
CommandList = []
CommandList.append(join)
CommandList.append(leave)
CommandList.append(play)
CommandList.append(skip)
CommandList.append(hentai)
CommandList.append(weather)