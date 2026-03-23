import feedparser, discord, random, logging, asyncio, shelve, traceback, sys, operator

logger = logging.getLogger(__name__)
hdlr = logging.FileHandler('/var/tmp/redditbot.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)

TOKEN = 'QjLxpIxnvOFC9ikQqQvcbI9jZ3H-wrSVURvj62nNq9J2nXXxIWaxU8sA9rs3QM3nzmwoA0UjSh1lRgMTFceT'
client = discord.Client()

database_file = '/var/tmp/articles.dat'

responses = ['no...', 'i said NO', 'i dont wanna', 'u shut up', 'fuck you', 'bitch', 'im telling', 'meanie', 'NO NO NO NO NO', 'no', 'NO!']
games = ['the waiting game', 'with myself', 'alone', 'with you', 'with fire', 'with knives', 'dead', 'god']
game_sufixes = [' | +redditbot', '', '']
fetch_time = 300
shutups = 0

spam_file = '/home/davidlones/wtf_trollface.txt'

def top_news(number):
	articles = {}
	for i in range(0,number):
		dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
		previous_articles = dbfile["articles"]

		d = feedparser.parse('https://www.reddit.com/r/news/.rss')
		article_link = d['entries'][i]['link']
		article_title = d['entries'][i]['title']

		if article_title not in previous_articles:
			previous_articles[article_title] = article_link
			articles[article_title] = article_link
			dbfile["articles"] = previous_articles

			print('\nNew Article: ' + article_title + '\n' + article_link)
			logger.warning('New Article: [' + article_title + ']')
			logger.warning('Link: [' + article_link + ']')

		dbfile.close()

	return articles

def tech_news(number):
	articles = {}
	for i in range(0,number):
		dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
		previous_articles = dbfile["articles"]

		d = feedparser.parse('https://www.reddit.com/r/technews/.rss')
		article_link = d['entries'][i]['link']
		article_title = d['entries'][i]['title']

		if article_title not in previous_articles:
			previous_articles[article_title] = article_link
			articles[article_title] = article_link
			dbfile["articles"] = previous_articles

			print('\nNew Article: ' + article_title + '\n' + article_link)
			logger.warning('New Article: [' + article_title + ']')
			logger.warning('Link: [' + article_link + ']')

		dbfile.close()
		
	return articles

async def fetch_news():
	await client.wait_until_ready()
	the_game1 = 'checking for news | +redditbot'
	the_game2 = 'the waiting game | +redditbot'
	channel = discord.Object(id='504726439799554049')
	while not client.is_closed:
		await asyncio.sleep(2)
		await client.change_presence(game=discord.Game(name=the_game1))
		print('.', end='', flush=True)

		dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
		previous_articles = dbfile["articles"]
		silence = previous_articles['silence']
		dbfile.close()

		news_db = top_news(2)
		news_articles = sorted(news_db.items(), key=operator.itemgetter(1))
		for article_title1, article_link1 in news_articles:
			# await client.send_message(channel, article_title1)
			# await client.send_message(channel, article_link1)
			await asyncio.sleep(1)

		tech_db = tech_news(2)
		tech_articles = sorted(tech_db.items(), key=operator.itemgetter(1))
		for article_title2, article_link2 in tech_articles:
			# await client.send_message(channel, article_title2)
			# await client.send_message(channel, article_link2)
			await asyncio.sleep(1)

		await client.change_presence(game=discord.Game(name=the_game2))
		await asyncio.sleep(fetch_time)

async def playing_games():
	await client.wait_until_ready()
	while not client.is_closed:
		random1 = random.SystemRandom()
		await asyncio.sleep(117)
		random2 = random.SystemRandom()
		game = random1.choice(games)
		game_sufix = random2.choice(game_sufixes)
		await client.change_presence(game=discord.Game(name = game + game_sufix))
		await asyncio.sleep(1700)

@client.event

async def on_message(message):
	global shutups
	# logger.info('[' + str(message.author) + ':' + str(message.channel) + '] ' + str(message.content))

	if message.author == client.user:
		return

	message_content_lower = str(message.content).lower()
	if ('redditbot' in message_content_lower) and (('shut up' in message_content_lower) or ('shutup' in message_content_lower)):
		shutups += 1
		temper = random.randint(7,17)

		if shutups < temper:
			random1 = random.SystemRandom()
			response = random1.choice(responses)		
			msg = response
			await client.send_message(message.channel, msg)
			logger.warning('Silence Requested: [' + str(message.author) + ':' + str(message.channel) + ']')

		else:
			channel = {}
			channel[1] = discord.Object(id='496375061134049294')
			channel[2] = discord.Object(id='504726439799554049')
			channel[3] = discord.Object(id='496438213469011970')
			channel[4] = discord.Object(id='496381705129689118')

			with open(spam_file) as f:
			    spam = f.readlines()
			spam = [x.strip() for x in spam]

			for line in spam:
				channels = [1,2,3,4]
				for x in channels:
					msg = line
					await client.send_message(channel[x], msg)


			subreddit = 'Ooer'
			await client.change_presence(game=discord.Game(name='r/' + subreddit))
			d = feedparser.parse('https://www.reddit.com/r/' + subreddit + '/.rss')

			x = 1
			random_sample = random.sample(range(21), 4)
			for article_number in random_sample:
				article_title = d['entries'][article_number]['title']
				article_link = d['entries'][article_number]['link']
				await client.send_message(channel[x], article_title)
				await client.send_message(channel[x], article_link)

				x += 1

			sys.exit()


	else:
		if message.content.startswith('+redditbot'):
			msg = 'Type r/<subreddit> in any channel for a random link.\nThe "News" channel is updated live with the top posts from r/news and r/technews.'
			await client.send_message(message.channel, msg)
			logger.warning('Help Requested: [' + str(message.author) + ':' + str(message.channel) + ']')

	if message.content.startswith('r/'):
		subreddit = message.content.lstrip('r/')
		await client.change_presence(game=discord.Game(name='r/' + subreddit))
		d = feedparser.parse('https://www.reddit.com/r/' + subreddit + '/.rss')
		article_number = random.randint(1,21)
		article_title = d['entries'][article_number]['title']
		article_link = d['entries'][article_number]['link']
		msg = article_title + '\n' + article_link
		await client.send_message(message.channel, msg)
		logger.warning('Link Requested: [' + str(message.author) + ':' + str(message.channel) + '] [' + article_link + ']')

@client.event
async def on_ready():
	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	previous_articles = dbfile["articles"]
	previous_articles['silence'] = 0
	dbfile['articles'] = previous_articles
	dbfile.close()

	logger.warning('STARTED')
	print("STARTED")
	logger.info('RedditBot is now active')
	print('RedditBot is now active')
	await client.change_presence(game=discord.Game(name='+redditbot'))

try:
	print('starting...', end=' ', flush=True)
	logger.warning('STARTING...')

	client.loop.create_task(fetch_news())
	client.loop.create_task(playing_games())
	client.run(TOKEN)

except:
	logger.error('Oops?')

	errorlog = traceback.format_exc()
	errorfmt = errorlog.splitlines()[-1]
	logger.debug(errorfmt)
	print(errorlog)

finally:
	logger.warning("REDDITBOT HAS STOPPED")
	print("REDDITBOT HAS STOPPED")

	errorlog = traceback.format_exc()
	errorfmt = errorlog.splitlines()[-1]
	logger.debug(errorfmt)
	print(errorlog)
