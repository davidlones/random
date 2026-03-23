# Work with Python 3.6
import discord, random, logging, traceback

logger = logging.getLogger(__name__)
hdlr = logging.FileHandler('/home/bot/logs/paulabot.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)

GREETINGS = ("paula", "hey", "hello", "hi", "howdy", "greetings", "sup", "what's up", "kill")
GREETING_RESPONSE = ["hello", "{0.author.mention} hello", "sup", "{0.author.mention} sup", "hey", "hey {0.author.mention}", "*nods*", "hi", "*walks past, doesn't hear you*", "Penny wants to see you in the ad office", "you aren't scheduled today {0.author.mention}", "Jamie called in", "Everyone called in today", "{0.author.mention} You're the only one scheduled today", "You have to cover wireless and photo all day", "the keys are missing", "Ray took the mc's back"]

def conversation(data):
    sentence = data#.translate(None, string.punctuation)
    words = sentence.split()
    msg1, msg2 = False, False
    for word in words:
        if word.lower() in GREETINGS:
            return random.choice(GREETING_RESPONSE)

    # if 'price changes' in sentence.lower():
    #     msg2 = 'There are ' + str(random.randint(1,1000001)) + ' late price changes'

    # if msg1 and msg2:
    #     return str(msg1 + '\n' + msg2)
    # if not msg1 and msg2:
    #     return str(msg2)
    # if msg1 and not msg2:
    #     return str(msg1)

TOKEN = 'DISCORD_TOKEN_REDACTED'

client = discord.Client()

@client.event
async def on_message(message):

    logger.info('[' + str(message.author) + ':' + str(message.channel) + '] ' + str(message.content))

    if message.author == client.user:
        return

    message_content_lower = str(message.content).lower()

    if 'paula' in message_content_lower:
        msg1_content = conversation(message.content)
        msg1 = msg1_content.format(message)
        await client.send_message(message.channel, msg1)

    if 'price changes' in message_content_lower:
        msg2_content = 'There are ' + str(random.randint(1,1001)) + ' late price changes'
        msg2 = msg2_content.format(message)
        await client.send_message(message.channel, msg2)

@client.event
async def on_ready():
    logger.warning('PAULABOT HAS STARTED')
    print("PAULABOT HAS STARTED")
    logger.info('PaulaBot logged in and doing Paula things')
    print('PaulaBot logged in and doing Paula things')


try:
    print('starting...')
    logger.warning('STARTING...')

    client.run(TOKEN)

except:
    logger.error('Oops?')

    errorlog = traceback.format_exc()
    errorfmt = errorlog.splitlines()[-1]
    logger.debug(errorfmt)
    print(errorlog)

finally:
    logger.warning("PAULABOT HAS STOPPED")
    print("PAULABOT HAS STOPPED")
