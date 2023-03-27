import discord
from discord.ext import commands
import configparser
config= configparser.RawConfigParser()
config.read('properties.properties')

intents = discord.Intents.default()
intents.messages = True
intents.members = True
intents.message_content = True
client=discord.Client(intents=intents)
TOKEN = config.get('configs','BOT_TOKEN')
bot = commands.Bot(command_prefix='',intents=discord.Intents.all())
bot_id = config.get('configs','BOT_ID')
voice_client=None
song_path= config.get('configs','SONG_PATH')
ffmpeg_location=config.get('configs','FFMPEG_LOCATION')
channel=None
what_do_you_want_responses=["لك حسطيزي","بيب ركز","معلش تكتب شو بدك","امر","شو بدك خال","شنو تريد"]
coming_to_vc_responses=["لحظة جاية","جاية يالله","جايتك"]
disconnecting_from_vc_responses= ["شعب ايري","I'll do like a banana and split","shehrazad out bitches","يالله باي بخاشي"]
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