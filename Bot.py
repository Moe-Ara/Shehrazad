import asyncio
import os

import CommandProcessor
import Commands
import Common

bot =Common.bot
TOKEN=Common.TOKEN
VoiceClient=None

def run_bot():
    @bot.event
    async def on_ready():

        print(f'{bot.user} is running')

    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return
        Common.context=await bot.get_context(message)
        VoiceClient = Common.context.voice_client
        # await CommandProcessor.ProcessCommand(sender, msg,channel,ctx=await bot.get_context(message))
        await CommandProcessor.ProcessCommand(message)

    # @bot.event
    # async def on_voicecommand(context):
    #     context
    @bot.event
    async def on_voice_state_update(member, before, after):
        voice_state=member.guild.voice_client
        if voice_state is None:
            return
        if len(voice_state.channel.members)==1:
            await voice_state.disconnect()

    bot.run(TOKEN)



if __name__=='__main__' :
    if os.name=="nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_bot())
