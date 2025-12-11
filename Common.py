import discord
from discord.ext import commands
import configparser
config= configparser.RawConfigParser()
config.read('properties.properties')

intents = discord.Intents.default()
intents.messages = True
intents.members = True
try:
    intents.message_content = True
except AttributeError:
    pass
client=discord.Client(intents=intents)
TOKEN = config.get('configs','BOT_TOKEN')
bot = commands.Bot(command_prefix='',intents=discord.Intents.all())
bot_id = config.get('configs','BOT_ID')
voice_client=None
song_path= config.get('configs','SONG_PATH')
ffmpeg_location=config.get('configs','FFMPEG_LOCATION')
channel=None
what_do_you_want_responses=["لك","بيب ركز","معلش تكتب شو بدك","امر","شو بدك خال","شنو تريد"]
coming_to_vc_responses=["لحظة جاية","جاية يالله","جايتك"]
disconnecting_from_vc_responses= ["I'll do like a banana and split","Shehzad out bitches", "يالله باي"]
command_prefix="s!"
context=None
is_song_playing=False
connected_to_voice_chat=False
ffmpeg_options = {
    'options':
        ' -vn'
        ' -filter_complex "[0:a]loudnorm[ln]; [ln]adeclip[ad];[ad]adeclick"'
        ' -b:a 192k'}
#; [0:a]adeclick
