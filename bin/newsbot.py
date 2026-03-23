import feedparser, discord, random, logging, asyncio

logger = logging.getLogger(__name__)
hdlr = logging.FileHandler('/var/tmp/newsbot.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)

TOKEN = 'DISCORD_TOKEN_REDACTED'

client = discord.Client()

async def my_background_task():
    await client.wait_until_ready()
    channel = discord.Object(id='504726439799554049')
    while not client.is_closed:
        previous_article = "Explosive Devices Found in Mail Sent to Hillary Clinton and Obama"
        d = feedparser.parse('https://www.reddit.com/r/news/.rss')
        article_link = d['entries'][0]['link']
        article_title = d['entries'][0]['title']

        if article_title != previous_article:
            print('New Article: ' + article_title + '\n' + article_link)
            logger.warning('New Article: [' + article_title + ']')
            logger.warning('Link Sent: [' + article_link + ']')
            await client.send_message(channel, article_link)
            previous_article = article_title

        await asyncio.sleep(600)

@client.event

async def on_message(message):

    # logger.info('[' + str(message.author) + ':' + str(message.channel) + '] ' + str(message.content))

    if message.author == client.user:
        return

    if message.content.startswith('+news'):
        d = feedparser.parse('https://www.reddit.com/r/news/.rss')
        article_number = random.randint(1,21)
        article_link = d['entries'][article_number]['link']
        msg_content = '{0.author.mention} ' + article_link
        msg = article_link
        await client.send_message(message.channel, msg)
        logger.warning('Link Requested: [' + str(message.author) + ':' + str(message.channel) + '] [' + article_link + ']')

@client.event
async def on_ready():
    logger.warning('NEWSBOT HAS STARTED')
    print("NEWSBOT HAS STARTED")
    logger.info('NewsBot is now active')
    print('NewsBot is now active')
    await client.change_presence(game=discord.Game(name='+news'))

try:
    print('starting...')
    logger.warning('STARTING...')

    client.loop.create_task(my_background_task())
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
