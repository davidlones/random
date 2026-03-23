import itertools
import threading
import logging
import time
import sys
import shelve
from pathlib import Path

logFormatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

fileHandler = logging.FileHandler('./logs/masterbot.log')
fileHandler.setFormatter(logFormatter)
fileHandler.setLevel(logging.DEBUG)
logger.addHandler(fileHandler)

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
consoleHandler.setLevel(logging.DEBUG)
logger.addHandler(consoleHandler)

logMsg = 'starting...'
logger.warning(logMsg)

database_file = './logs/masterbot.dat'
databaseCheck = Path(database_file)
if not databaseCheck.is_file():
	logMsg = 'database not found'
	logger.warning(logMsg)
	logMsg = 'creating new database...'
	logger.warning(logMsg)
	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	dbfile["servers"] = {}
	dbfile.close()

if databaseCheck.is_file():
	logMsg = 'database loaded'
	logger.warning(logMsg)

done = False
def animate():
    for c in itertools.cycle(['.  ', ' . ', '  .', ' . ']):
        if done:
            break
        sys.stdout.write('\rconnecting' + c)
        sys.stdout.flush()
        time.sleep(0.1)
    sys.stdout.write('\rconnected\r')

import feedparser
import random
import asyncio
import traceback
import operator
import subprocess
import math
import schedule
import re


logMsg = 'connecting...'
logger.warning(logMsg)

TOKEN = 'DISCORD_TOKEN_REDACTED'

t = threading.Thread(target=animate)
t.start()

import discord
client = discord.Client(intents=discord.Intents.default())

done = True
logMsg = 'connected'
logger.warning(logMsg)

leaderboards = {}
leaderboards['D&D'] = discord.Object(id='632590240787333156')



# async def updateDB(serverName, userName, message, messageContent):
# 	await client.wait_until_ready()
# 	serverCheck = True
# 	userCheck = True

# 	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
# 	serverData = dbfile["servers"]
# 	if serverName not in serverData:
# 		serverCheck = False
# 		userData = {}
# 		serverData[serverName] = {'users':userData}
# 		dbfile["servers"] = serverData

# 		print('\nNew Server: ' + serverName)
# 		logger.warning('New Server: [' + serverName + ']')

# 	thisServer = serverData[serverName]
# 	userData = thisServer['users']
# 	if 'newrules' not in thisServer:
# 		thisServer['newrules'] = False
# 		serverData[serverName] = thisServer
# 		dbfile['servers'] = serverData

# 		print('\nNew Rules prep Initiated: ' + serverName)
# 		logger.warning('New Rules prep Initiated: [' + serverName + ']')

# 	newrules = thisServer['newrules']

# 	if userName not in userData:
# 		userCheck = False
	
# 		userData[userName] = {'level':0,
# 							'xp':1,
# 							'achievements':[],
# 							'inspiration':0,
# 							'dicerolls':1,
# 							'wordcount':0,
# 							'words':{}}

# 		thisServer['users'] = userData
# 		serverData[serverName] = thisServer

# 		dbfile['servers'] = serverData

# 		print('New User: ' + str(userName))
# 		logger.warning('New User: [' + str(userName) + ']')

# 	thisUser = userData[userName]

# 	level = thisUser['level']
# 	xp = thisUser['xp']
# 	achievements = thisUser['achievements']
# 	inspiration = thisUser['inspiration']
# 	dicerolls = thisUser['dicerolls']
# 	wordcount = thisUser['wordcount']
# 	words = thisUser['words']

# 	messageWords = re.findall(r'\w+', messageContent)
# 	for word in messageWords:
# 		if word not in words:
# 			words[word] = 1
# 		else:
# 			words[word] = words[word] + 1

# 	messageWordcount = len(messageWords)

# 	wordcount = wordcount + messageWordcount
# 	previouslevel = level

# 	achievements = [item.replace('DELETED', 'DELETION') for item in achievements]

# 	achievements = [item.replace('DELETEION', 'DELETION') for item in achievements]

# 	allachievements = []
# 	alldicerolls = []
# 	for theUser in userData:
# 		eachUser = userData[theUser]
# 		userDicerolls = eachUser['dicerolls']
# 		alldicerolls.append(userDicerolls)
# 		for achievement in eachUser['achievements']:
# 			allachievements.append(achievement)
# 	userCount = len(userData)
# 	transmigrations = allachievements.count('TRANSMIGRATION')

# 	dicerollsSorted = sorted(alldicerolls, reverse = True)
# 	dicerollPosition =  dicerollsSorted.index(dicerolls) + 1

# 	if serverName in leaderboards:
# 		leaderboard = leaderboards[serverName]
# 	else:
# 		leaderboard = message.channel


# 	if transmigrations > int(userCount/2) and not newrules:
# 		newrules = True
# 		msg_content = "yeeess... new rules are at play, indeed"
# 		msg = msg_content.format(message)
# 		await client.send_message(leaderboard, msg)

# 	if newrules or "TRANSMIGRATION" in achievements:

# 		advantage = int(20/dicerollPosition) + (inspiration*level)

# 		xp = int(int(xp) + ((messageWordcount+advantage) // (level+1)))
# 		level = int(xp//444)


# 	else:
# 		xp = int(xp + ((messageWordcount+(random.randint(1,int(dicerolls)))) //(level+1)) + (inspiration*level))
# 		level = int(xp//111)


# 	if  level > previouslevel:
# 		msg_content = "{0.author.mention} has reached **Level " + str(level) + "!**"
# 		msg = msg_content.format(message)
# 		await client.send_message(leaderboard, msg)

# 	if level == 0:
# 		if "Rolled Initiative!" not in achievements:
# 			if "+roll d20" in messageContent or "+roll 1d20" in messageContent:
# 				achievements.append('Rolled Initiative!')
# 				msg_content = "***Achievement!***\n*{0.author.mention} rolled initiative!*"
# 				msg = msg_content.format(message)
# 				await client.send_message(message.channel, msg)

# 			elif "Lost The Game" not in achievements:
# 				msg_content = "*{0.author.mention} a bot approches, roll initiative!*"
# 				msg = msg_content.format(message)
# 				await client.send_message(message.channel, msg)

# 		if "Lost The Game" not in achievements:
# 			achievements.append('Lost The Game')
# 			msg_content = "***Achievement!***\n*{0.author.mention} has lost The Game*"
# 			msg = msg_content.format(message)
# 			await client.send_message(message.channel, msg)

# 	if level == 1:
# 		if "NOT a 0" not in achievements:
# 			achievements.append('NOT a 0')
# 			msg_content = ("***Achievement!***\n" +
# 						"*Let it be known: {0.author.mention} is a 1, **NOT** a 0!*")
# 			msg = msg_content.format(message)
# 			await client.send_message(message.channel, msg)

# 	if level == 42:
# 		if "42" not in achievements:
# 			achievements.append('42')
# 			msg_content = ("***Achievement!***\n" +
# 						"*{0.author.mention} has found the answer to life, the Universe, and everything...*")
# 			msg = msg_content.format(message)
# 			await client.send_message(message.channel, msg)

# 	if level > 238900:
# 		if "To the Moon" not in achievements:
# 			achievements.append('To the Moon')
# 			msg_content = ("***Achievement!***\n" +
# 						"*WTF?! {0.author.mention} just shot past the moon!!!*")
# 			msg = msg_content.format(message)
# 			await client.send_message(message.channel, msg)

# 	if level > 46508000000:
# 		if "Ya broke physics..." not in achievements:
# 			achievements.append('Ya broke physics...')
# 			msg_content = ("***Achievement!***\n" +
# 						"*{0.author.mention} has escaped the observable universe!!! **46.508 billion** light years away.*")
# 			msg = msg_content.format(message)
# 			await client.send_message(message.channel, msg)

# 	if level > 1000000000000000:
# 		if "DELETION" not in achievements:
# 			achievements.append('DELETION')
# 			msg_content = "*You reeally shouldn't break physics like that...*"
# 			msg = msg_content.format(message)
# 			await client.send_message(message.channel, msg)

# 			level = 0
# 			xp = 1
# 			dicerolls = 1

# 			msg_content = "*{0.author.mention}'s XP has been **deleted***"
# 			msg = msg_content.format(message)
# 			await client.send_message(message.channel, msg)

# 		else:
# 			level = 0
# 			xp = 1
# 			dicerolls = 1
# 			achievements.append('TRANSMIGRATION')

# 			msg_content = "*The Transmigration of {0.author.mention} is complete...*\nThe rules governing you microcosm have changed."
# 			msg = msg_content.format(message)
# 			await client.send_message(message.channel, msg)


# 	thisUser['level'] = level
# 	thisUser['xp'] = xp
# 	thisUser['achievements'] = achievements
# 	thisUser['inspiration'] = inspiration
# 	thisUser['dicerolls'] = dicerolls
# 	thisUser['wordcount'] = wordcount
# 	thisUser['words'] = words

# 	userData[userName] = thisUser
# 	thisServer['users'] = userData
# 	thisServer['newrules'] = newrules
# 	serverData[serverName] = thisServer
# 	dbfile['servers'] = serverData

# 	dbfile.close()

# 	return serverCheck, userCheck


#####################################################



#####################################################
lastMessage = ""
@client.event
async def on_message(message):
	lastMessage = message
	shut_down = False
	try:

		try:
			logger.info('[' + str(message.author) + ':' + str(message.channel) + ':' + str(message.guild.name) + '] ' + str(message.content))

			msg = input("> ")
			if msg != "":
				await lastMessage.channel.send(msg)

		except:
			return

		if message.author == client.user:
			return

		# message_content_lower = str(message.content).lower()
		# await updateDB(message.guild.name, message.author, message, message_content_lower)

	except:
		errorlog = traceback.format_exc()
		errorfmt = errorlog.splitlines()[-1]
		logger.error(errorfmt)
		print(errorlog)


# async def replyMessage():
# 	await client.wait_until_ready()
# 	msg = input("> ")
# 	if msg != "":
# 		if lastMessage != "":
# 			await lastMessage.channel.send(msg)



@client.event
async def on_ready():
	logMsg = 'MasterBot is now active'
	logger.warning(logMsg)
	await client.change_presence(activity=discord.Game(name='im alive'))

try:
	done = True
	logMsg = 'starting tasks...'
	logger.warning(logMsg)

	# client.loop.create_task(loop_audio(voice_channel, voice_file))
	# client.loop.create_task(playing_games())
	# client.loop.create_task(scheduled_tasks())
	
	# client.loop.create_task(replyMessage())
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
