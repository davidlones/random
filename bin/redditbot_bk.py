import feedparser, discord, random, logging, asyncio, shelve, traceback, sys, operator, subprocess, math, schedule, time

logger = logging.getLogger(__name__)
hdlr = logging.FileHandler('/home/bot/logs/redditbot.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)

TOKEN = 'DISCORD_TOKEN_REDACTED'
client = discord.Client()

database_file = '/home/bot/logs/articles.dat'

responses = ['no...', 'i said NO', 'i dont wanna', 'u shut up', 'fuck you', 'bitch', 'im telling', 'meanie', 'NO NO NO NO NO', 'no', 'NO!']
games = ['the waiting game', 'the waiting game', 'the waiting game', 'a game', 'a game', 'u can mute the news', 'with myself', 'dead', 'god']
game_sufixes = [' | +redditbot', '', '']

top = 1
tech = 1
gaming = 1
newstime1 = '10:00 AM'
shutups = 0

spam_file = '/home/bot/spam/shitter.txt'
voice_file = '/home/bot/media/3301.wav'
voice_channel = '497192443406581760'

def thetime(t):
	if t in time.strftime("%I:%M %p"):
		return True
	else:
		return False

def getLength(file):
    cmd = 'ffprobe -i {} -show_entries format=duration -v quiet -of csv="p=0"'.format(file)
    output = subprocess.check_output(
        cmd,
        shell=True,
        stderr=subprocess.STDOUT
    )
    return int(math.ceil(float(output)))

def gaming_news(number):
	articles = {}
	for i in range(0,number):
		dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
		previous_articles = dbfile["articles"]

		try:
			d = feedparser.parse('https://www.reddit.com/r/gamingnews/.rss')
			article_link = d['entries'][i]['link']
			article_title = d['entries'][i]['title']
		except:
			article_link = 'https://www.reddit.com/r/pepethefrog/comments/9ss57h/the_aftermath/'
			article_title = 'an error occured - error 8'

			logger.error('an error occured - error 8')

			errorlog = traceback.format_exc()
			errorfmt = errorlog.splitlines()[-1]
			logger.debug(errorfmt)
			print(errorlog)

		if article_title not in previous_articles:
			previous_articles[article_title] = article_link
			articles[article_title] = article_link
			dbfile["articles"] = previous_articles

			print('\nNew Article: ' + article_title + '\n' + article_link)
			logger.warning('New Article: [' + article_title + ']')
			logger.warning('Link: [' + article_link + ']')

		dbfile.close()

	return articles


def top_news(number):
	articles = {}
	for i in range(0,number):
		dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
		previous_articles = dbfile["articles"]

		try:
			d = feedparser.parse('https://www.reddit.com/r/news/.rss')
			article_link = d['entries'][i]['link']
			article_title = d['entries'][i]['title']
		except:
			article_link = 'https://www.reddit.com/r/pepethefrog/comments/9ss57h/the_aftermath/'
			article_title = 'an error occured - error 8'

			logger.error('an error occured - error 8')

			errorlog = traceback.format_exc()
			errorfmt = errorlog.splitlines()[-1]
			logger.debug(errorfmt)
			print(errorlog)

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

		try:
			d = feedparser.parse('https://www.reddit.com/r/technews/.rss')
			article_link = d['entries'][i]['link']
			article_title = d['entries'][i]['title']
		except:
			article_link = 'https://www.reddit.com/r/pepethefrog/comments/9ss57h/the_aftermath/'
			article_title = 'an error occured - error 9'
			logger.error('an error occured - error 9')

			errorlog = traceback.format_exc()
			errorfmt = errorlog.splitlines()[-1]
			logger.debug(errorfmt)
			print(errorlog)


		if article_title not in previous_articles:
			previous_articles[article_title] = article_link
			articles[article_title] = article_link
			dbfile["articles"] = previous_articles

			print('\nNew Article: ' + article_title + '\n' + article_link)
			logger.warning('New Article: [' + article_title + ']')
			logger.warning('Link: [' + article_link + ']')

		dbfile.close()
		
	return articles

async def fetch_news(top, tech, gaming):
	await client.wait_until_ready()
	the_game1 = 'checking for news | +redditbot'
	the_game2 = 'u can mute the news | +redditbot'
	channel = discord.Object(id='504726439799554049')

	await asyncio.sleep(2)
	await client.change_presence(game=discord.Game(name=the_game1))
	print('.', end='', flush=True)

	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	previous_articles = dbfile["articles"]
	silence = previous_articles['silence']
	dbfile.close()


	news_db = top_news(top)

	news_articles = sorted(news_db.items(), key=operator.itemgetter(1))
	for article_title1, article_link1 in news_articles:
		await client.send_message(channel, article_title1 + '\n' + article_link1)
		await asyncio.sleep(1)

	tech_db = tech_news(tech)
	tech_articles = sorted(tech_db.items(), key=operator.itemgetter(1))
	for article_title2, article_link2 in tech_articles:
		await client.send_message(channel, article_title2 + '\n' + article_link2)
		await asyncio.sleep(1)
		
	gaming_db = gaming_news(gaming)
	gaming_articles = sorted(gaming_db.items(), key=operator.itemgetter(1))
	for article_title3, article_link3 in gaming_articles:
		await client.send_message(channel, article_title3 + '\n' + article_link3)
		await asyncio.sleep(1)

	print('fetching news')
	await client.change_presence(game=discord.Game(name=the_game2))

async def scheduled_tasks():
	await client.wait_until_ready()

	while True:
		if thetime(newstime1):
			await fetch_news(top,tech,gaming)

		await asyncio.sleep(60)

async def play_audio(channel_id,file):
	await client.wait_until_ready()
	voice_channel = discord.Object(id=channel_id)
	voice = await client.join_voice_channel(voice_channel)
	player = voice.create_ffmpeg_player(file)
	player.start()

async def loop_audio(channel_id,file):
#	while not client.is_closed:
#	try:
	await client.wait_until_ready()
	length = getLength(file)
	voice_channel = discord.Object(id=channel_id)
	voice = await client.join_voice_channel(voice_channel)
	logMsg = "looping audio: '" + file + "'"
	print(logMsg)
	logger.info(logMsg)
	while not client.is_closed:
		player = voice.create_ffmpeg_player(file, options='-loglevel 0')
		player.start()
		await asyncio.sleep(length)
#except:
#		for x in client.voice_clients:
#			await x.disconnect()



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
		temper = random.randint(3,17)

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
			channel[3] = discord.Object(id='510549825310162972')
			channel[4] = discord.Object(id='496438213469011970')
			channel[5] = discord.Object(id='496381705129689118')

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
				await client.send_message(channel[x], article_title + '\n' + article_link)

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

	logMsg = 'STARTED'
	logger.warning(logMsg)
	print(logMsg)

	logMsg = 'RedditBot is now active'
	logger.info(logMsg)
	print(logMsg)
	await client.change_presence(game=discord.Game(name='+redditbot'))

try:
	logMsg = 'starting...'
	print(logMsg, end=' ', flush=True)
	logger.warning(logMsg)

	client.loop.create_task(loop_audio(voice_channel, voice_file))
	client.loop.create_task(playing_games())
	client.loop.create_task(scheduled_tasks())
	
	client.run(TOKEN)

except:
	logger.error('Oops?')

	errorlog = traceback.format_exc()
	errorfmt = errorlog.splitlines()[-1]
	logger.debug(errorfmt)
	print(errorlog)

finally:
	logMsg = 'REDDITBOT HAS STOPPED'
	logger.warning(logMsg)
	print(logMsg)

	errorlog = traceback.format_exc()
	errorfmt = errorlog.splitlines()[-1]
	logger.debug(errorfmt)
	print(errorlog)
