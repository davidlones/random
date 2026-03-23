import feedparser, discord, random, logging, asyncio, shelve, traceback, sys, operator, subprocess, math, schedule, time

logger = logging.getLogger(__name__)
hdlr = logging.FileHandler('/home/davidlones/logs/redditbot.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)

top = 1
tech = 2
memes = 1
news_time = '11:00'
meme_time = '16:52'
shutups = 0

def my_memes(number):
	articles = {}
	for i in range(0,number):
#		dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
#		previous_articles = dbfile["articles"]

		try:
			d = feedparser.parse('http://inline-reddit.com/feed/?multireddit=redditbot&user=anon876094')
			article_number = random.randint(1,21)
			article_title = d['entries'][article_number]['title']
			article_link = d['entries'][article_number]['link']
		except:
			article_link = 'https://www.reddit.com/r/pepethefrog/comments/9ss57h/the_aftermath/'
			article_title = 'an error occured - error 8'

			logger.error('an error occured - error 8')

			errorlog = traceback.format_exc()
			errorfmt = errorlog.splitlines()[-1]
			logger.debug(errorfmt)
			print(errorlog)

#		if article_title not in previous_articles:
#			previous_articles[article_title] = article_link
#			articles[article_title] = article_link
#			dbfile["articles"] = previous_articles

		print('\nNew Article: ' + article_title + '\n' + article_link)
		logger.warning('New Article: [' + article_title + ']')
		logger.warning('Link: [' + article_link + ']')

#		dbfile.close()

	return articles

def fetch_memes(memes):
#	await client.wait_until_ready()
	the_game1 = 'memes MEMES! | +redditbot'
	the_game2 = 'i do memes now | +redditbot'
#	channel = discord.Object(id='623586352063184947')

#	await asyncio.sleep(2)
#	await client.change_presence(game=discord.Game(name=the_game1))
	print('.', end='', flush=True)

#	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
#	previous_articles = dbfile["articles"]
#	silence = previous_articles['silence']
#	dbfile.close()


	meme_db = my_memes(memes)

	meme_links = sorted(meme_db.items(), key=operator.itemgetter(1))
	for meme_title, meme_link in meme_links:
#		await client.send_message(channel, meme_title + '\n' + meme_link)
		#await asyncio.sleep(1)
		time.sleep(1)
		print(meme_title)
		print(meme_link)

#	await client.change_presence(game=discord.Game(name=the_game2))

def scheduled_tasks():
	print("started")
	while True:
		current_time = time.strftime("%H:%M")
		#print(current_time)
		if current_time == meme_time:
			fetch_memes(memes)
		time.sleep(60)

try:
	logMsg = 'starting...'
	print(logMsg, end=' ', flush=True)
	logger.warning(logMsg)

#	client.loop.create_task(loop_audio(voice_channel, voice_file))
#	client.loop.create_task(playing_games())
	scheduled_tasks()	
#	client.run(TOKEN)

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