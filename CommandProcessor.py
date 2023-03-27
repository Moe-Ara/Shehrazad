import random

import discord
import Common
import Commands

bot_id = Common.bot_id


async def ProcessCommand(message):
    sender = message.author
    channel = message.channel
    msg = str(message.content)

    if not msg.__contains__(Common.command_prefix):
        await onMessage(sender, msg, channel)
        return
    else:
        msg = str(msg).split(Common.command_prefix)[1]
        if msg == "":
            response = Common.what_do_you_want_responses[random.randint(0,
                                                                        len(Common.what_do_you_want_responses))]
            await channel.send(str(response))
        # remove leading spaces
        msg = msg.strip()
        cmd = msg.split(" ")[0]
        arg = msg.split(" ")[1] if len(msg.split(" ")) > 1 else ""
        for i in Commands.CommandList:
            if (i.aliases.__contains__(str(cmd))):
                if i == Commands.join or i == Commands.leave:
                    await i.execute(Common.context)
                elif i == Commands.play:
                    allLinks = []
                    links = msg.split(" ")
                    for link in links:
                        if link.__contains__("https"):
                            allLinks.append(link)
                    if (len(allLinks) == 0):
                        await channel.send("ممكن تعطيني الرابط")
                        return
                    await i.execute(allLinks)
                else:
                    await i.execute(arg)
        return
    await onMessage(sender, msg, channel)


async def onMessage(sender, message, channel):
    if message == 'Shehrazad' or message == 'shosho':
        await channel.send('عيونا ؟')

    if message == "hello shehrazad" or message == "hello shosho":
        await channel.send('Hello, I hope I can be of service today :)')
    if message == '\U0001F642':
        await channel.send('\U0001F643')

    if sender.id == 520998774714400784:
        await channel.send(sender.mention + 'kol 2ere')

    if sender.id == 410821336072716288 and (message == "kol 5ra" or message == "kol 2ere"):
        await channel.send('ولاك')

    if sender.id == 283104778077208583:
        await channel.send(sender.mention + "Sameer kol 5ra")
