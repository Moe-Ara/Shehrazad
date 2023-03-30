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
    def __init__(self, cmdText, aliases, arguments, function, description="None",usage_example=""):
        self.CommandText = cmdText.lower()
        self.aliases = [cmdText] + aliases
        self.CommandArguments = arguments
        self.function = function
        self.description=description
        self.usage_example=usage_example
    def toString(self):
        return f"Command {self.CommandText} {self.description}; it has the following aliases {str(self.aliases)}"
    def execute(self, args):
        return self.function(args)


##FUNCTIONS
def is_connected(ctx):
    voice_client = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
    return voice_client and voice_client.is_connected()


async def leaveCommand(ctx):
    if is_connected(ctx):
        for member in ctx.author.voice.channel.members:
            if str(member.id) == bot_id:
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
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredquality': '192',
                }]
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

#TODO: extend this so that it can also be used for specific commands
async def helpCommand(cmd=""):
    msg = ""
    if cmd=="":
        for command in CommandList:
            msg = msg+ command.toString()+"\n"
        await Common.context.send(msg)
        return

#TODO: extend commands by providing usage_example
###COMMANDS
join = Command('join', ['j', 'connect'], [], joinCommand, "joins a voice channel")
leave = Command('leave', ['l', 'disconnect'], [], leaveCommand, "leaves the voice channel")
play = Command('play', ['p', 'l3be'], [], playCommand, "plays a song. You need to provide a url")
skip = Command('skip', ['s'], [], skipCommand, "skips the playing song")
hentai= Command('hentai', ['h','sexme','sendnudes','sn'], [], sendHentaiCommand, "sends some hentai photos ;)")
weather= Command('weather', ['getweather','temp', 'w', 'forcast'],[],getWeather, "checks the weather. You need to provide a city")
help= Command('help',['h'],[],helpCommand,"helps you with commands")
# COMMAND LIST
CommandList = []
CommandList.append(join)
CommandList.append(leave)
CommandList.append(play)
CommandList.append(skip)
CommandList.append(hentai)
CommandList.append(weather)