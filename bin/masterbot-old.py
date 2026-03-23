import feedparser, discord, random, logging, asyncio, shelve, traceback, sys, operator, subprocess, math, schedule, time, re
from pathlib import Path

logger = logging.getLogger(__name__)
hdlr = logging.FileHandler('./logs/masterbot.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)

TOKEN = 'DISCORD_TOKEN_REDACTED'
client = discord.Client()

database_file = './logs/masterbot.dat'
databaseCheck = Path(database_file)
if not databaseCheck.is_file():
	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	dbfile["servers"] = {}
	dbfile.close()

# d = enchant.Dict("en_US")

def rolldice(diceString, serverName, userName):
	try:
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
			result = eval(str(result) + mathString1)

			if mathString2:
				result = eval(str(mathResult1) + mathString2)
				theResult.append(('(' + str(result) + mathString1 + ')' + mathString2))

			else:
				theResult.append((str(result) + mathString1))

		theResult.append(result)


		if result:
			dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
			serverData = dbfile["servers"]

			thisServer = serverData[serverName]
			userData = thisServer['users']
			thisUser = userData[userName]
			dicerolls = thisUser['dicerolls']

			dicerolls = dicerolls + result

			thisUser['dicerolls'] = dicerolls
			userData[userName] = thisUser
			thisServer['users'] = userData
			serverData[serverName] = thisServer

			dbfile['servers'] = serverData
			dbfile.close()

		return theResult

	except:
		logger.error('Error: malformed diceString?')
		return False


def getStats(serverName, userName):
	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	serverData = dbfile["servers"]
	dbfile.close()

	thisServer = serverData[serverName]
	userData = thisServer['users']
	thisUser = userData[userName]

	return thisUser




async def updateDB(serverName, userName, message, messageContent):
	await client.wait_until_ready()
	serverCheck = True
	userCheck = True

	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	serverData = dbfile["servers"]
	if serverName not in serverData:
		serverCheck = False
		userData = {}
		serverData[serverName] = {'users':userData}
		dbfile["servers"] = serverData

		print('\nNew Server: ' + serverName)
		logger.warning('New Server: [' + serverName + ']')

	thisServer = serverData[serverName]
	userData = thisServer['users']

	if userName not in userData:
		userCheck = False
	
		userData[userName] = {'level':0,
							'xp':1,
							'achievements':[],
							'inspiration':0,
							'dicerolls':1,
							'wordcount':0,
							'words':{}}

		thisServer['users'] = userData
		serverData[serverName] = thisServer

		dbfile['servers'] = serverData

		print('New User: ' + str(userName))
		logger.warning('New User: [' + str(userName) + ']')

	thisUser = userData[userName]

	level = thisUser['level']
	xp = thisUser['xp']
	achievements = thisUser['achievements']
	inspiration = thisUser['inspiration']
	dicerolls = thisUser['dicerolls']
	wordcount = thisUser['wordcount']
	words = thisUser['words']

	messageWords = re.findall(r'\w+', messageContent)
	for word in messageWords:
		if word not in words:
			words[word] = 1
		else:
			words[word] = words[word] + 1

	messageWordcount = len(messageWords)

	wordcount = wordcount + messageWordcount

	xp = int(xp + ((messageWordcount+(random.randint(1,dicerolls)))/(level+1)) + (inspiration*level))

	previouslevel = level
	level = int(xp/111)

	if  level > previouslevel:
		msg_content = "{0.author.mention} has reached **Level " + str(level) + "!**"
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)

	if level == 0:
		if "Rolled Initiative!" not in achievements:
			if "+roll d20" in messageContent or "+roll 1d20" in messageContent:
				achievements.append('Rolled Initiative!')
				msg_content = "***Achievement!***\n*{0.author.mention} rolled initiative!*"
				msg = msg_content.format(message)
				await client.send_message(message.channel, msg)

			else:
				msg_content = "*{0.author.mention} a bot approches, roll initiative!*"
				msg = msg_content.format(message)
				await client.send_message(message.channel, msg)

		if "Lost The Game" not in achievements:
			achievements.append('Lost The Game')
			msg_content = "***Achievement!***\n*{0.author.mention} has lost The Game*"
			msg = msg_content.format(message)
			await client.send_message(message.channel, msg)

	if level == 1:
		if "NOT a 0" not in achievements:
			achievements.append('NOT a 0')
			msg_content = "***Achievement!***\n*Let it be known: {0.author.mention} is a 1, **NOT** a 0!*"
			msg = msg_content.format(message)
			await client.send_message(message.channel, msg)

	if level == 42:
		if "42" not in achievements:
			achievements.append('42')
			msg_content = "***Achievement!***\n*{0.author.mention} has found the answer to life, the Universe, and everything...*"
			msg = msg_content.format(message)
			await client.send_message(message.channel, msg)



	thisUser['level'] = level
	thisUser['xp'] = xp
	thisUser['achievements'] = achievements
	thisUser['inspiration'] = inspiration
	thisUser['dicerolls'] = dicerolls
	thisUser['wordcount'] = wordcount
	thisUser['words'] = words

	userData[userName] = thisUser
	thisServer['users'] = userData
	serverData[serverName] = thisServer
	dbfile['servers'] = serverData

	dbfile.close()

	return serverCheck, userCheck



@client.event
async def on_message(message):

	logger.info('[' + str(message.author) + ':' + str(message.channel) + ':' + str(message.server.name) + '] ' + str(message.content))
	

	if message.author == client.user:
		return

	message_content_lower = str(message.content).lower()
	await updateDB(message.server.name, message.author, message, message_content_lower)

	if '+stats' in message_content_lower:
		userStats = getStats(message.server.name, message.author)

		msg_content = ("{0.author.mention}\n" +
						"Level: **" + str(userStats['level']) + "**\n" +
						"XP: **" + str(userStats['xp']) + "**\n" +
						"Achievements: \n**" + str(userStats['achievements']) + "**\n")

		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)

	if '+allstats' in message_content_lower:
		userStats = getStats(message.server.name, message.author)

		msg_content = ("{0.author.mention}\n" +
						"Level: **" + str(userStats['level']) + "**\n" +
						"XP: **" + str(userStats['xp']) + "**\n" +
						"Inspiration: **" + str(userStats['inspiration']) + "**\n" +
						"Dice Roll Total: **" + str(userStats['dicerolls']) + "**\n" +
						"Word Count: **" + str(userStats['wordcount']) + "**\n" +
						"Achievements: \n**" + str(userStats['achievements']) + "**\n")

		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)

	if '+inspiration' in message_content_lower:
		msg_content = "Sorry, that's not finished yet. <@287783002380173313> got tired and went to bed :("
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)


	if '+runtests' in message_content_lower:
		await client.change_presence(game=discord.Game(name='tests'))
		logger.warning('Testing Initiated: [' + str(message.author) + ':' + str(message.channel) + ':' + str(message.server.name) + '] ')

		serverTest, userTest, testData = runTests(message.server.name, message.author)
		
		logger.debug(str(serverTest))
		msg_content = "{0.author.mention} " + str(serverTest)
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)

		logger.debug(str(userTest))
		msg_content = "{0.author.mention} " + str(userTest)
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)

		logger.debug(str(testData))
		msg_content = "{0.author.mention} " + str(testData)
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)
		await client.change_presence(game=discord.Game(name='+masterbot'))

	if '+help' in message_content_lower:
		logger.warning('Help Requested: [' + str(message.author) + ':' + str(message.channel) + ':' + str(message.server.name) + '] ')
		msg_content = ("{0.author.mention} *You want cheats???*\n" +
						"Ok. Fine.\n" +
						"\n" +
						"Here's the command list:\n" +
						"**+masterbot**\n" +
						"**+roll**\n" +
						"**+inspiration**\n" +
						"**+stats**\n" +
						"**+allstats**\n" +
						"\n" +
						"More interesting things will be added soon.")
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)



	if '+masterbot' in message_content_lower:
		logger.warning('Dialog Initiated: [' + str(message.author) + ':' + str(message.channel) + ':' + str(message.server.name) + '] ')
		msg_content = "{0.author.mention} hello friend"
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)
		await asyncio.sleep(2)
		msg_content = "In multitasking computer operating systems, a 'daemon' is a computer program that runs as a background process, rather than being under the direct control of an interactive user."
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)
		await asyncio.sleep(7)
		msg_content = "Daemons. *They don’t stop working.* They’re always active. They *seduce.* They *manipulate.*"
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)
		await asyncio.sleep(11)
		msg_content = "\n***They own us.***"
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)
		await asyncio.sleep(5)

		dice = random.randint(1,1000)
		face = random.randint(1,20)
		add = random.randint(1,100)
		mult = random.randint(2,10)
		msg_content = "I think it's time you rolled the dice\n"
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)
		await asyncio.sleep(2)
		msg_content = "+roll " + str(dice) + 'd' + str(face) + ' +' + str(add) + ' /' + str(mult)
		msg = msg_content.format(message)
		await client.send_message(message.channel, msg)

		diceroll = rolldice(msg_content.split())
		x = 1
		for i in diceroll:
			if x == len(diceroll):
				msg_content = '{0.author.mention} **You rolled:** '
			else:
				msg_content = ''

			buff = [str(i)[b:b+2000] for b in range(0, len(str(i)), 2000)]
			if len(buff) > 1:
				for buff_content in buff:
					msg = buff_content.format(message)
					await client.send_message(message.channel, msg)
			else:
				i = buff[0]
				msg_content = msg_content + '\n' + str(i)
				msg = msg_content.format(message)
				await client.send_message(message.channel, msg)
			x += 1



	if message.content.startswith('+roll'):
		diceroll = rolldice(message_content_lower.split(), message.server.name, message.author)
		if diceroll:
			x = 1
			for i in diceroll:
				if x == len(diceroll):
					msg_content = '{0.author.mention} You rolled: '
				else:
					msg_content = ''

				buff = [str(i)[b:b+2000] for b in range(0, len(str(i)), 2000)]
				if len(buff) > 1:
					if len(buff) < 10:
						for buff_content in buff:
							msg = buff_content.format(message)
							await client.send_message(message.channel, msg)
					else:
						msg_content = "**[LOTS OF DICE]**"
						msg = msg_content.format(message)
						await client.send_message(message.channel, msg)
				else:
					i = buff[0]
					msg_content = msg_content + '**' + str(i) + '**'
					msg = msg_content.format(message)
					await client.send_message(message.channel, msg)
				x += 1
		else:
			msg_content = "Usage: *+roll <#>d<#> <+-#> <x/#>*"
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

	logMsg = 'MasterBot is now active'
	logger.info(logMsg)
	print(logMsg)
	await client.change_presence(game=discord.Game(name='+masterbot'))

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
	logMsg = 'MASTERBOT HAS STOPPED'
	logger.warning(logMsg)
	print(logMsg)

	errorlog = traceback.format_exc()
	errorfmt = errorlog.splitlines()[-1]
	logger.debug(errorfmt)
	print(errorlog)
