import feedparser, discord, random, logging, asyncio, shelve, traceback, sys, operator, subprocess, math, schedule, time

logger = logging.getLogger(__name__)
hdlr = logging.FileHandler('./logs/autobot.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)

TOKEN = 'DISCORD_TOKEN_REDACTED'
client = discord.Client()

def rolldice(diceString):
	theResult = []
	theString = diceString[1]
	diceStringParsed = theString.split('d')

	if len(diceString) > 2:
		mathString1 = diceString[2].translate(str.maketrans({"x": r"*", "X": r"*"}))

		if len(diceString) > 3:
			mathString2 = diceString[3].translate(str.maketrans({"x": r"*", "X": r"*"}))
		else:
			mathString2 = False
	else:
		mathString1 = False

	try:
		dice = int(diceStringParsed[0])
	except ValueError:
		dice = 1

	sides = int(diceStringParsed[1])

	results = []

	if dice:
		while dice > 0:
			roll = random.randint(1,sides)
			results.append(roll)
			dice = dice - 1
	else:
		roll = random.randint(1,sides)
		results.append(roll)

	result = sum(results)

	theResult.append(results)

	if mathString1:
		mathResult1 = eval(str(result) + mathString1)

		if mathString2:
			mathResult2 = eval(str(mathResult1) + mathString2)
			theResult.append(('(' + str(result) + mathString1 + ')' + mathString2))
			theResult.append(mathResult2)

		else:
			theResult.append((str(result) + mathString1))
			theResult.append(mathResult1)

	else:
		theResult.append(result)

	return theResult


@client.event
async def on_message(message):
	logger.info('[' + str(message.author) + ':' + str(message.channel) + '] ' + str(message.content))

	if message.author == client.user:
		return

	message_content_lower = str(message.content).lower()
	if message.content.startswith('+roll'):
		diceroll = rolldice(message_content_lower.split())
		msg_content = '{0.author.mention}'
		for i in diceroll:
			msg_content = msg_content + '\n' + str(i)

		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)


@client.event
async def on_ready():
	# dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	# previous_articles = dbfile["articles"]
	# previous_articles['silence'] = 0
	# dbfile['articles'] = previous_articles
	# dbfile.close()

	logMsg = 'STARTED'
	logger.warning(logMsg)
	print(logMsg)

	logMsg = 'AutoBot is now active'
	logger.info(logMsg)
	print(logMsg)
	await client.change_presence(game=discord.Game(name='beta'))

try:
	logMsg = 'starting...'
	print(logMsg, end=' ', flush=True)
	logger.warning(logMsg)

	# client.loop.create_task(loop_audio(voice_channel, voice_file))
	# client.loop.create_task(playing_games())
	# client.loop.create_task(scheduled_tasks())
	
	client.run(TOKEN)

except:
	logger.error('Oops?')

	errorlog = traceback.format_exc()
	errorfmt = errorlog.splitlines()[-1]
	logger.debug(errorfmt)
	print(errorlog)

finally:
	logMsg = 'AUTOBOT HAS STOPPED'
	logger.warning(logMsg)
	print(logMsg)

	errorlog = traceback.format_exc()
	errorfmt = errorlog.splitlines()[-1]
	logger.debug(errorfmt)
	print(errorlog)
