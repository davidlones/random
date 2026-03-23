import feedparser, discord, random, logging, asyncio, shelve, traceback, sys, operator, subprocess, math, schedule, time, re
import itertools
import threading
import socket
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
consoleHandler.setLevel(logging.WARNING)
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
    for c in itertools.cycle(['.  \r', ' . \r', '  .\r', ' . \r']):
        if done:
            break
        sys.stdout.write('' + c)
        sys.stdout.flush()
        time.sleep(0.1)
    sys.stdout.write('')

import feedparser
import random
import asyncio
import shelve
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
client = discord.Client()

done = True

statusUpdate = 'recompiling'
statusMode = discord.Status.idle

logMsg = 'connected'
logger.warning(logMsg)

leaderboards = {}
leaderboards['D&D'] = discord.Object(id='632590240787333156')
leaderboards['test'] = discord.Object(id='638406764034785281')

diceboards = {}
diceboards['D&D'] = discord.Object(id='517721444944314368')
diceboards['test'] = discord.Object(id='638401830057017374')

welcomescreens = {}
welcomescreens['D&D'] = discord.Object(id='631179210043424768')
welcomescreens['test'] = discord.Object(id='638539950333231115')

def ping(ip_address="www.google.com"):
	p = subprocess.Popen(["ping", "-c", "5", ip_address], stdout = subprocess.PIPE)
	return p.communicate()[0]


def rolldice(diceString, serverName, userName):
	try:
		theString = diceString[1]
		diceStringParsed = theString.split('d')

		try:
			dice = int(diceStringParsed[0])
		except ValueError:
			dice = 1

		sides = int(diceStringParsed[1])


		if dice > 100000:
			raise ValueError('number of dice is greater than 100000')
		if sides > 10000:
			raise ValueError('number of sides is greater than 10000')

		results = []

		if dice < 0:
			dice = dice * -1

		if dice > 0:
			while dice > 0:
				if sides > 0:
					roll = random.randint(1,sides)
					results.append(roll)
				elif sides == 0:
					results.append(0)
				else:
					negsides = sides * -1
					roll = random.randint(1,negsides) * -1
					results.append(roll)
				dice -= 1

		elif dice == 0:
			results.append(0)
		else:
			raise ValueError('double negative?')

		result = sum(results)
		firstResult = result

		mathString = str(result)
		argNum = 0
		for diceArg in diceString:
			if argNum > 1:
				mathArg = diceString[argNum].translate(str.maketrans({"x": r"*", "×":r"*", "÷":r"/", "^": r"**"}))
				mathString = '(' + mathString + ')' + mathArg
				result = eval(mathString)
			argNum += 1

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

		return results, mathString, result

	except:
		errorlog = traceback.format_exc()
		errorfmt = errorlog.splitlines()[-1]
		logger.debug(errorfmt)
		print(errorlog)
		
		raise ValueError('diceroll failed')


def getStats(serverName, userName):
	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	serverData = dbfile["servers"]
	dbfile.close()

	thisServer = serverData[serverName]
	userData = thisServer['users']
	thisUser = userData[userName]

	return thisUser


def override(serverName, userName, target, dbitem, dbvalue):

	dbcheck = True

	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	serverData = dbfile["servers"]
	thisServer = serverData[serverName]
	userData = thisServer['users']

	try:
		if target in userData:
			thisUser = userData[target]
			thisUser[dbitem] = dbvalue
			userData[target] = thisUser

			thisServer['users'] = userData
			serverData[serverName] = thisServer
			dbfile['servers'] = serverData

		else:
			userData[target] = {'level':0,
								'xp':1,
								'achievements':[],
								'inspiration':0,
								'dicerolls':1,
								'wordcount':0,
								'words':{}}

			thisUser = userData[target]

			thisUser[dbitem] = dbvalue
			userData[target] = thisUser

			thisServer['users'] = userData
			serverData[serverName] = thisServer
			dbfile['servers'] = serverData

	except:
		errorlog = traceback.format_exc()
		errorfmt = errorlog.splitlines()[-1]
		logger.debug(errorfmt)
		print(errorlog)

		dbcheck = False

	dbfile.close()

	return dbcheck

def rpstats(serverName, target, strength, dexterity, constitution, intelligence, wisdom, charisma):
	userCheck = False

	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	serverData = dbfile["servers"]
	thisServer = serverData[serverName]
	userData = thisServer['users']

	if target in userData:
		thisUser = userData[target]
		level = thisUser['level']

		statrolls = {'strength':int(strength), 'dexterity':int(dexterity), 'constitution':int(constitution), 'intelligence':int(intelligence), 'wisdom':int(wisdom), 'charisma':int(charisma)}
		statlist = {}
		for stat, roll in statrolls.items():
			if (roll == 1):
				mod = -5 
			elif ((roll == 2) or (roll == 3)):
				mod = -4
			elif ((roll == 4) or (roll == 5)):
				mod = -3
			elif ((roll == 6) or (roll == 7)):
				mod = -2
			elif ((roll == 8) or (roll == 9)):
				mod = -1
			elif ((roll == 10) or (roll == 11)):
				mod = 0
			elif ((roll == 12) or (roll == 13)):
				mod = 1
			elif ((roll == 14) or (roll == 15)):
				mod = 2
			elif ((roll == 16) or (roll == 17)):
				mod = 3
			elif ((roll == 18) or (roll == 19)):
				mod = 4
			elif ((roll == 20) or (roll == 21)):
				mod = 5
			elif ((roll == 22) or (roll == 23)):
				mod = 6
			elif ((roll == 24) or (roll == 25)):
				mod = 7
			elif ((roll == 26) or (roll == 27)):
				mod = 8
			elif ((roll == 28) or (roll == 29)):
				mod = 9
			elif (roll == 30):
				mod = 10
			else:
				mod = 11
				userCheck = False

			thisUser[stat] = [roll, mod]

		con = thisUser['constitution']
		dex = thisUser['dexterity']

		thisUser['ac'] = (10+dex[1])
		thisUser['hp'] = ((level*10)+(level*con[1]))

		userData[target] = thisUser

		userCheck = True

	else:
		logMsg = "[" + str(userName) + "] not in database?"
		logger.warning(logMsg)

	thisServer['users'] = userData
	serverData[serverName] = thisServer
	dbfile['servers'] = serverData

	dbfile.close()

	return userCheck

def hitpoints(serverName, userName, target=False, damage=0, hit=None):
	hitCheck = False

	if not target:
		target = userName
		hit = None

	if hit == 1:
		target = userName

	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	serverData = dbfile["servers"]
	thisServer = serverData[serverName]
	userData = thisServer['users']

	if target in userData:
		thisUser = userData[target]
		level = thisUser['level']

		if 'ac' in thisUser:
			ac = thisUser['ac']
			hp = thisUser['hp']

			if hit == None or hit > ac:
				hp += damage
				hitCheck = True

			thisUser['hp'] = hp
			userData[target] = thisUser
		else:
			hitCheck = 'rp?'
			hp = None

	else:
		logMsg = "[" + str(target) + "] not in database?"
		logger.warning(logMsg)

	thisServer['users'] = userData
	serverData[serverName] = thisServer
	dbfile['servers'] = serverData

	dbfile.close()

	return hitCheck, hp

def giveInspiration(serverName, userName, targets):
	userCheck = []

	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	serverData = dbfile["servers"]
	thisServer = serverData[serverName]
	userData = thisServer['users']

	for target in targets:
		if target in userData:
			thisUser = userData[target]
			inspiration = thisUser['inspiration']
			inspiration += 1
			thisUser['inspiration'] = inspiration
			userData[target] = thisUser

			userCheck.append(target)

		else:
			logMsg = "[" + str(target) + "] not in database?"
			logger.warning(logMsg)

	thisServer['users'] = userData
	serverData[serverName] = thisServer
	dbfile['servers'] = serverData

	dbfile.close()

	return userCheck

def giveAchievement(serverName, userName, achievement, targets, conditions):
	dbcheck = True

	dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
	serverData = dbfile["servers"]
	thisServer = serverData[serverName]
	userData = thisServer['users']

	if 'achievements' not in thisServer:
		thisServer['achievements'] = {}

	serverAchievements = thisServer['achievements']


	try:
		if targets:
			for target in targets:
				if target in userData:
					thisUser = userData[target]
					userAchievements = thisUser['achievements']
					userAchievements.append(achievement)

					thisUser['achievements'] = userAchievements
					userData[target] = thisUser
					thisServer['users'] = userData
					serverData[serverName] = thisServer
					dbfile['servers'] = serverData

				else:
					logMsg = "[" + str(target) + "] not in database?"
					logger.warning(logMsg)

					dbcheck = False

		elif conditions:
			serverAchievements[achievement] = conditions
			thisServer['achievements'] = serverAchievements

			serverData[serverName] = thisServer
			dbfile['servers'] = serverData

		else:
			dbcheck = False

	except:
		errorlog = traceback.format_exc()
		errorfmt = errorlog.splitlines()[-1]
		logger.debug(errorfmt)
		print(errorlog)

		dbcheck = False

	dbfile.close()

	return dbcheck




async def updateDB(serverName, userName, message, messageContent, leaderboard):
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
	if 'newrules' not in thisServer:
		thisServer['newrules'] = False
		serverData[serverName] = thisServer
		dbfile['servers'] = serverData

		print('\nNew Rules prep Initiated: ' + serverName)
		logger.warning('New Rules prep Initiated: [' + serverName + ']')

	newrules = thisServer['newrules']

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
	previouslevel = level

	achievements = [item.replace('DELETED', 'DELETION') for item in achievements]

	achievements = [item.replace('DELETEION', 'DELETION') for item in achievements]

	allachievements = []
	alldicerolls = []
	for theUser in userData:
		eachUser = userData[theUser]
		userDicerolls = eachUser['dicerolls']
		alldicerolls.append(userDicerolls)
		for achievement in eachUser['achievements']:
			allachievements.append(achievement)
	userCount = len(userData)
	transmigrations = allachievements.count('TRANSMIGRATION')

	dicerollsSorted = sorted(alldicerolls, reverse = True)
	dicerollPosition =  dicerollsSorted.index(dicerolls) + 1

	if serverName in leaderboards:
		leaderboard = leaderboards[serverName]
	else:
		leaderboard = message.channel

	if serverName in welcomescreens:
		welcome = welcomescreens[serverName]
	else:
		welcome = message.channel


	if transmigrations > int(userCount/2) and not newrules:
		newrules = True
		msg_content = "yeeess... new rules are at play, indeed"
		msg = msg_content.format(message)
		await client.send_message(leaderboard, msg)

	if newrules or "TRANSMIGRATION" in achievements:

		advantage = int(20/dicerollPosition) + (inspiration*level)

		xp = int(int(xp) + ((messageWordcount+advantage) // (level+1)))
		level = int(xp//444)


	else:
		xp = int(xp + ((messageWordcount+(random.randint(1,int(dicerolls)))) //(level+1)) + (inspiration*level))
		level = int(xp//111)


	if  level > previouslevel:
		msg_content = "{0.author.mention} has reached **Level " + str(level) + "!**"
		msg = msg_content.format(message)
		await client.send_message(leaderboard, msg)

	if level == 0:
		if "Rolled Initiative!" not in achievements:
			if "+roll d20" in messageContent or "+roll 1d20" in messageContent:
				achievements.append('Rolled Initiative!')
				msg_content = "***Achievement!***\n*{0.author.mention} rolled initiative!*"
				msg = msg_content.format(message)
				await client.send_message(leaderboard, msg)

			elif "Lost The Game" not in achievements:
				msg_content = "*{0.author.mention} a bot approches, roll initiative!*"
				msg = msg_content.format(message)
				await client.send_message(welcome, msg)

		if "Lost The Game" not in achievements:
			achievements.append('Lost The Game')
			msg_content = "***Achievement!***\n*{0.author.mention} has lost The Game*"
			msg = msg_content.format(message)
			await client.send_message(leaderboard, msg)

	if level == 1:
		if "NOT a 0" not in achievements:
			achievements.append('NOT a 0')
			msg_content = ("***Achievement!***\n" +
						"*Let it be known: {0.author.mention} is a 1, **NOT** a 0!*")
			msg = msg_content.format(message)
			await client.send_message(leaderboard, msg)

	if level == 42:
		if "42" not in achievements:
			achievements.append('42')
			msg_content = ("***Achievement!***\n" +
						"*{0.author.mention} has found the answer to life, the Universe, and everything...*")
			msg = msg_content.format(message)
			await client.send_message(leaderboard, msg)

	if level > 238900:
		if "To the Moon" not in achievements:
			achievements.append('To the Moon')
			msg_content = ("***Achievement!***\n" +
						"*WTF?! {0.author.mention} just shot past the moon!!!*")
			msg = msg_content.format(message)
			await client.send_message(leaderboard, msg)

	if level > 46508000000:
		if "Ya broke physics..." not in achievements:
			achievements.append('Ya broke physics...')
			msg_content = ("***Achievement!***\n" +
						"*{0.author.mention} has escaped the observable universe!!! **46.508 billion** light years away.*")
			msg = msg_content.format(message)
			await client.send_message(leaderboard, msg)

	if level > 1000000000000000:
		if "DELETION" not in achievements:
			achievements.append('DELETION')
			msg_content = "*You reeally shouldn't break physics like that...*"
			msg = msg_content.format(message)
			await client.send_message(leaderboard, msg)

			level = 0
			xp = 1
			dicerolls = 1

			msg_content = "*{0.author.mention}'s XP has been **deleted***"
			msg = msg_content.format(message)
			await client.send_message(leaderboard, msg)

		else:
			level = 0
			xp = 1
			dicerolls = 1
			achievements.append('TRANSMIGRATION')

			msg_content = "*The Transmigration of {0.author.mention} is complete...*\nThe rules governing you microcosm have changed."
			msg = msg_content.format(message)
			await client.send_message(leaderboard, msg)


	thisUser['level'] = level
	thisUser['xp'] = xp
	thisUser['achievements'] = achievements
	thisUser['inspiration'] = inspiration
	thisUser['dicerolls'] = dicerolls
	thisUser['wordcount'] = wordcount
	thisUser['words'] = words

	userData[userName] = thisUser
	thisServer['users'] = userData
	thisServer['newrules'] = newrules
	serverData[serverName] = thisServer
	dbfile['servers'] = serverData

	dbfile.close()

	return serverCheck, userCheck



@client.event
async def on_message(message):
	shut_down = False
	try:
		if message.server.name in leaderboards:
			leaderboard = leaderboards[message.server.name]
		else:
			leaderboard = message.channel

		if message.server.name in diceboards:
			diceboard = diceboards[message.server.name]
		else:
			diceboard = message.channel

		try:
			logger.info('[' + str(message.author) + ':' + str(message.channel) + ':' + str(message.server.name) + '] ' + str(message.content))

		except:
			msg_content = "{0.author.mention} wat?"
			msg = msg_content.format(message)
			await client.send_message(message.channel, msg)


		if message.author == client.user:
			return

		message_content_lower = str(message.content).lower()
		await updateDB(message.server.name, message.author, message, message_content_lower, leaderboard)


		if message_content_lower.startswith('+setstats'):
			arguments = message_content_lower.split()
			if len(arguments) == 7 or (len(arguments) == 8 and message.mentions):
				strength = arguments[1]
				dexterity = arguments[2]
				constitution = arguments[3]
				intelligence = arguments[4]
				wisdom = arguments[5]
				charisma = arguments[6]

				if message.mentions:
					target = message.mentions[0]
				else:
					target = message.author

				setstats = rpstats(message.server.name, target, strength, dexterity, constitution, intelligence, wisdom, charisma)

				userStats = getStats(message.server.name, target)
				
				userAch = userStats['achievements']
				achievements_fmt = ''
				for i in userAch:
					achievements_fmt = (achievements_fmt + '[' + i + ']\n              ')

				strength_fmt = str(userStats['strength'][0]) + ', ' + '{0:+d}'.format(userStats['strength'][1])
				dexterity_fmt = str(userStats['dexterity'][0]) + ', ' + '{0:+d}'.format(userStats['dexterity'][1])
				constitution_fmt = str(userStats['constitution'][0]) + ', ' + '{0:+d}'.format(userStats['constitution'][1])
				intelligence_fmt = str(userStats['intelligence'][0]) + ', ' + '{0:+d}'.format(userStats['intelligence'][1])
				wisdom_fmt = str(userStats['wisdom'][0]) + ', ' + '{0:+d}'.format(userStats['wisdom'][1])
				charisma_fmt = str(userStats['charisma'][0]) + ', ' + '{0:+d}'.format(userStats['charisma'][1])

				msg_content = ("{0.author.mention}\n" +
										"```Level:        [" + str(userStats['level']) + "]\n" +
											"XP:           [" + str(userStats['xp']) + "]\n" +
											"Inspiration:  [" + str(userStats['inspiration']) + "]\n" +
											"HP:           [" + str(userStats['hp']) + "]\n" +
											"AC:           [" + str(userStats['ac']) + "]\n" +
											"\n" +
											"Strength:     [" + strength_fmt + "]\n" +
											"Dexterity:    [" + dexterity_fmt + "]\n" +
											"Constitution: [" + constitution_fmt + "]\n" +
											"Intelligence: [" + intelligence_fmt + "]\n" +
											"Wisdom:       [" + wisdom_fmt + "]\n" +
											"Charisma:     [" + charisma_fmt + "]\n" +
											"\n" +
											"Achievements: " + achievements_fmt + "```\n")

				msg = msg_content.format(message)
				await client.send_message(message.channel, msg)



			else:
				msg_content = ("{0.author.mention}\n" +
									"```\nUsage: +roll 6d20\n" +
									"       +setstats [strength] [dexterity] [constitution] [intelligence] [wisdom] [charisma]```")
				msg = msg_content.format(message)

				await client.send_message(message.channel, msg)

		if message_content_lower.startswith('+attack'):
			arguments = message_content_lower.split()
			if message.mentions and len(arguments) > 2:
				for target in message.mentions:
					try:
						hit = int(arguments[-2])
					except ValueError:
						hit = None
					damage = int(arguments[-1]) * -1
					attacked, hp = hitpoints(message.server.name, message.author, target, damage, hit)

					if attacked == 'rp?':
						msg_content = (target.mention + " please set your rp stats to continue.\n" +
									"```\nUsage: +roll 6d20\n" +
									"       +setstats [strength] [dexterity] [constitution] [intelligence] [wisdom] [charisma]```")
						msg = msg_content.format(message)
						await client.send_message(message.channel, msg)

					elif attacked:
						if damage < 0:
							damage = (damage*-1)
							if hit == 1 or target == message.author:
								msg_content = '*{0.author.mention} hits themself in the face, takes **' + str(damage) + ' damage!***'
							else:
								msg_content = '*' + target.mention + ' takes **' + str(damage) + ' damage!***'
						elif damage == 0:
							msg_content = '*{0.author.mention} grabs ' + target.mention + " by the ass and leans in for a kiss!*"
						else:
							msg_content = '*{0.author.mention} gives ' + target.mention + ' **' + str(damage) + ' HP!***'

						msg = msg_content.format(message)
						await client.send_message(message.channel, msg)

						if hp < 1:
							if target == message.author:
								msg_content = '***{0.author.mention} COMMITED SUICIDE?!***'

							elif str(target) == 'MasterBot#8632':
								msg_content = '***WARNING: CRITICAL FALURE!***'
								shut_down = True

							else:
								msg_content = '***' + target.mention + ' BE DEAD!***'
							
							msg = msg_content.format(message)
							await client.send_message(message.channel, msg)


					else:

						msg_content = '*' + target.mention + ' deflects the hit!*'
						msg = msg_content.format(message)

						await client.send_message(message.channel, msg)

			else:
				msg_content = ("{0.author.mention}\n" +
								"```Usage: +attack [@username] [roll-to-hit] [damage]\n" +
								"       +attack [@username] [damage]```")
				msg = msg_content.format(message)

				await client.send_message(message.channel, msg)


		if message_content_lower.startswith('+heal'):
			arguments = message_content_lower.split()
			if message.mentions and len(arguments) > 2:
				for target in message.mentions:
					healing = int(arguments[-1])
					healed, hp = hitpoints(message.server.name, message.author, target, healing)

					if healed == 'rp?':
						msg_content = (target.mention + ", please set your character stats to continue.\n" +
									"```\nUsage: +roll 6d20\n" +
									"       +setstats [strength] [dexterity] [constitution] [intelligence] [wisdom] [charisma]```")
						msg = msg_content.format(message)
						await client.send_message(message.channel, msg)

					elif healed:
						if healing > 0:
							msg_content = '*{0.author.mention} gives ' + target.mention + ' **' + str(healing) + ' HP!***'
						elif healing == 0:
							msg_content = '*{0.author.mention} provides moral support to ' + target.mention + '.'
						else:
							msg_content = '*{0.author.mention} gives ' + target.mention + ' a mislabeled bottle of Instant Damage! They take **' + str(healing) + ' DAMAGE!***'

						msg = msg_content.format(message)
						await client.send_message(message.channel, msg)

						if hp < 1:
							msg_content = '*' + target.mention + ' is only... mostly dead*'

						else:
							msg_content = '***' + target.mention + ' IS ALIVE!***'
							
						msg = msg_content.format(message)
						await client.send_message(message.channel, msg)


					else:
						msg_content = '*' + target.mention + ' rejects the heals?*'
						msg = msg_content.format(message)

						await client.send_message(message.channel, msg)

			else:
				msg_content = ("{0.author.mention}\n" +
							"```Usage: +heal [@username] [heals]```")
				msg = msg_content.format(message)

				await client.send_message(message.channel, msg)


		if message_content_lower.startswith('+achievement'):
			arguments = message_content_lower.split()
			messageQuotes = re.findall(r'"(.*?)"', str(message.content))
			if str(message.channel) == 'masterbot-cli':
				if message.mentions and messageQuotes:
					achievement = messageQuotes[0]
					achievementMessage = messageQuotes[1]
					targets = message.mentions
					conditions = False
					gaveAchievement = giveAchievement(message.server.name, message.author, achievement, targets, conditions)
					if gaveAchievement:
						msg_content = ("***Achievement!***\n" +
									'*' + targets[0].mention + ' ' + achievementMessage + '*')
						msg = msg_content.format(message)
						await client.send_message(leaderboard, msg)
						giveusage = False
					else:
						giveusage = True

				else:
					giveusage = True

				if giveusage:
					msg_content = ('{0.author.mention}\n' +
								'```Usage: +achievement ["achievement"] [@username] ["message"]```')
					msg = msg_content.format(message)

					await client.send_message(message.channel, msg)
			else:
				msg_content = ("{0.author.mention}\n```WARNING: UNAUTHORIZED USE OF COMMAND!```")
				msg = msg_content.format(message)

				await client.send_message(message.channel, msg)


		if message_content_lower.startswith('+override'):
			arguments = message_content_lower.split()
			if str(message.channel) == 'masterbot-cli':
				if message.mentions and len(arguments) == 4:
					target = message.mentions[0]
					dbitem = arguments[2]
					dbvalue = arguments[3]
					dboverride = override(message.server.name, message.author, target, dbitem, int(dbvalue))

					if dboverride:
						msg_content = "database altered successfully"
						msg = msg_content.format(message)

						await client.send_message(message.channel, msg)

					else:
						msg_content = "an error occured, please check logs for details"
						msg = msg_content.format(message)

						await client.send_message(message.channel, msg)

				else:
					msg_content = ("```WARNING: CONSULT THE BOTLORD BEFORE USE\n" +
								"Usage: +override [@username] [key] [value]\n" +
								"Keys: xp, inspiration, dicerolls```")
					msg = msg_content.format(message)

					await client.send_message(message.channel, msg)
			else:
				msg_content = ("{0.author.mention}\n```WARNING: UNAUTHORIZED USE OF COMMAND!```")
				msg = msg_content.format(message)

				await client.send_message(message.channel, msg)

		if '+stats' in message_content_lower:
			userStats = getStats(message.server.name, message.author)

			userAch = userStats['achievements']
			achievements_fmt = ''
			for i in userAch:
				achievements_fmt = (achievements_fmt + '[' + i + ']\n              ')

			strength_fmt = str(userStats['strength'][0]) + ', ' + '{0:+d}'.format(userStats['strength'][1])
			dexterity_fmt = str(userStats['dexterity'][0]) + ', ' + '{0:+d}'.format(userStats['dexterity'][1])
			constitution_fmt = str(userStats['constitution'][0]) + ', ' + '{0:+d}'.format(userStats['constitution'][1])
			intelligence_fmt = str(userStats['intelligence'][0]) + ', ' + '{0:+d}'.format(userStats['intelligence'][1])
			wisdom_fmt = str(userStats['wisdom'][0]) + ', ' + '{0:+d}'.format(userStats['wisdom'][1])
			charisma_fmt = str(userStats['charisma'][0]) + ', ' + '{0:+d}'.format(userStats['charisma'][1])

			if 'ac' in userStats:
				msg_content = ("{0.author.mention}\n" +
								"```Level:        [" + str(userStats['level']) + "]\n" +
									"XP:           [" + str(userStats['xp']) + "]\n" +
									"Inspiration:  [" + str(userStats['inspiration']) + "]\n" +
									"HP:           [" + str(userStats['hp']) + "]\n" +
									"AC:           [" + str(userStats['ac']) + "]\n" +
									"\n" +
									"Strength:     [" + strength_fmt + "]\n" +
									"Dexterity:    [" + dexterity_fmt + "]\n" +
									"Constitution: [" + constitution_fmt + "]\n" +
									"Intelligence: [" + intelligence_fmt + "]\n" +
									"Wisdom:       [" + wisdom_fmt + "]\n" +
									"Charisma:     [" + charisma_fmt + "]\n" +
									"\n" +
									"Achievements: " + achievements_fmt + "```\n")

			else:
				msg_content = ("{0.author.mention} please set your rp stats to continue.\n" +
							"```\nUsage: +roll 6d20\n" +
							"       +setstats [strength] [dexterity] [constitution] [intelligence] [wisdom] [charisma]```")


			msg = msg_content.format(message)
			await client.send_message(message.channel, msg)

		if message_content_lower.startswith('+ '):

			achievement = 'failure'
			achievementMessage = 'failed at life...'
			targets = [message.author]
			conditions = False
			gaveAchievement = giveAchievement(message.server.name, message.author, achievement, targets, conditions)

			msg_content = ("***Achievement!***\n" +
						"*{0.author.mention} " + achievementMessage + "*")
			msg = msg_content.format(message)
			await client.send_message(message.channel, msg)


		if message_content_lower.startswith('+allstats'):
			if message.mentions:
				targets = message.mentions
			else:
				targets = {message.author}


			for target in targets:
				userStats = getStats(message.server.name, target)
				words = userStats['words']
				topWords = sorted(words, key=words.get, reverse=True)
				revtopWords = sorted(words, key=words.get, reverse=False)
				commonWords = topWords[50:60]
				uncommonWords = revtopWords[:10]

				userAch = userStats['achievements']
				achievements_fmt = ''
				for i in userAch:
					achievements_fmt = (achievements_fmt + '[' + i + ']\n                 ')
				strength_fmt = str(userStats['strength'][0]) + ', ' + '{0:+d}'.format(userStats['strength'][1])
				dexterity_fmt = str(userStats['dexterity'][0]) + ', ' + '{0:+d}'.format(userStats['dexterity'][1])
				constitution_fmt = str(userStats['constitution'][0]) + ', ' + '{0:+d}'.format(userStats['constitution'][1])
				intelligence_fmt = str(userStats['intelligence'][0]) + ', ' + '{0:+d}'.format(userStats['intelligence'][1])
				wisdom_fmt = str(userStats['wisdom'][0]) + ', ' + '{0:+d}'.format(userStats['wisdom'][1])
				charisma_fmt = str(userStats['charisma'][0]) + ', ' + '{0:+d}'.format(userStats['charisma'][1])





				if 'ac' in userStats:
					msg_content = ("{0.author.mention}\n" +
									"```Username:        [" + str(target) + "]\n" +
										"Level:           [" + str(userStats['level']) + "]\n" +
										"XP:              [" + str(userStats['xp']) + "]\n" +
										"Inspiration:     [" + str(userStats['inspiration']) + "]\n" +
										"HP:              [" + str(userStats['hp']) + "]\n" +
										"AC:              [" + str(userStats['ac']) + "]\n" +
										"\n" +
										"Strength:        [" + strength_fmt + "]\n" +
										"Dexterity:       [" + dexterity_fmt + "]\n" +
										"Constitution:    [" + constitution_fmt + "]\n" +
										"Intelligence:    [" + intelligence_fmt + "]\n" +
										"Wisdom:          [" + wisdom_fmt + "]\n" +
										"Charisma:        [" + charisma_fmt + "]\n" +
										"\n" +
										"Achievements:    " + achievements_fmt + "\n"
										"\n" +
										"Super Secret Stats:\n" +
										"\n" +
										"Dice Roll Total: [" + str(userStats['dicerolls']) + "]\n" +
										"Word Count:      [" + str(userStats['wordcount']) + "]\n" +
										"Common Words:    " + str(commonWords) + "\n" +
										"Uncommon Words:  " + str(uncommonWords) + "```\n")

					msg = msg_content.format(message)

				else:
					msg_content = ("{0.author.mention} please set your rp stats to continue.\n" +
									"```\nUsage: +roll 6d20\n" +
									"       +setstats [strength] [dexterity] [constitution] [intelligence] [wisdom] [charisma]```")

					msg = msg_content.format(message)

				try:
					await client.send_message(message.channel, msg)
				except:
					logger.error('400?')
					logger.debug("Message Conflict: [" + msg + "]")

					errorlog = traceback.format_exc()
					errorfmt = errorlog.splitlines()[-1]
					logger.debug(errorfmt)
					print(errorlog)

					msg_content = errorfmt
					msg = msg_content.format(message)

					await client.send_message(message.channel, msg)

					msg_content = "check logs for malformed message"
					msg = msg_content.format(message)

					await client.send_message(message.channel, msg)


		if message_content_lower.startswith('+inspiration'):
			if str(message.channel) == 'masterbot-cli':
				try:
					gaveInspiration = giveInspiration(message.server.name, message.author, message.mentions)

					if gaveInspiration:
						for target in gaveInspiration:
							msg_content = "***" + target.mention + " gets inspiration!***"
							msg = msg_content.format(message)
							await client.send_message(leaderboard, msg)

					else:
						msg_content = ("```Usage: +inspiration [@username]```")
						msg = msg_content.format(message)
						await client.send_message(message.channel, msg)

				except:
					errorlog = traceback.format_exc()
					errorfmt = errorlog.splitlines()[-1]
					logger.error(errorfmt)
					print(errorlog)

					msg_content = ("```Usage: +inspiration [@username]```")
					msg = msg_content.format(message)
					await client.send_message(message.channel, msg)

			else:
				msg_content = ("{0.author.mention}\n```WARNING: UNAUTHORIZED USE OF COMMAND!```")
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

		if message_content_lower.startswith('+help'):
			logger.warning('Help Requested: [' + str(message.author) + ':' + str(message.channel) + ':' + str(message.server.name) + '] ')
			msg_content = ("{0.author.mention}\n" +
							"```Player Commands:\n" +
							"+help\n" +
							"+masterbot\n" +
							"+roll\n" +
							"+setstats\n"+
							"+stats\n" +
							"+allstats\n" +
							"+attack\n" +
							"+heal\n" +
							"\n" +
							"Admin Commands:\n" +
							"+inspiration\n" +
							"+achievement\n" +
							"+override```")
			msg = msg_content.format(message)
			await client.send_message(message.channel, msg)



		if '+egg' in message_content_lower:
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

			dice = random.randint(1,10000)
			face = 20
			add = random.randint(1,100)

			msg_content = "I think it's time you rolled the dice\n"
			msg = msg_content.format(message)
			await client.send_message(message.channel, msg)

			await asyncio.sleep(2)
			msg_content = "+roll " + str(dice) + 'd' + str(face) + ' +' + str(add) 
			msg = msg_content.format(message)
			await client.send_message(message.channel, msg)

			diceroll = rolldice(msg_content.split(), message.server.name, message.author)
			if diceroll:
				results, mathString, result = diceroll
				buff = [str(results)[b:b+2000] for b in range(0, len(str(results)), 2000)]
				if len(buff) > 10:
					msg_content = "**[LOTS OF DICE]**"
					msg = msg_content.format(message)
					await client.send_message(message.channel, msg)
				elif result == 0:
					msg_content = "**[NOT ENOUGH DICE]**"
					msg = msg_content.format(message)
					await client.send_message(message.channel, msg)
				else:
					for buff_content in buff:
						msg_content = buff_content
						msg = msg_content.format(message)
						await client.send_message(message.channel, msg)

				msg_content = mathString
				msg = msg_content.format(message)
				await client.send_message(message.channel, msg)
				
				msg_content = "{0.author.mention} rolled: **" + str(result) + "**"
				msg = msg_content.format(message)
				await client.send_message(message.channel, msg)
				
			else:
				raise ValueError("diceroll failed")


		if message_content_lower.startswith('+roll'):
			try:
				diceroll = rolldice(message_content_lower.split(), message.server.name, message.author)
				if diceroll:
					results, mathString, result = diceroll
					buff = [str(results)[b:b+2000] for b in range(0, len(str(results)), 2000)]
					if len(buff) > 10:
						msg_content = "**[LOTS OF DICE]**"
						msg = msg_content.format(message)
						await client.send_message(diceboard, msg)
					elif result == 0:
						msg_content = "**[NOT ENOUGH DICE]**"
						msg = msg_content.format(message)
						await client.send_message(diceboard, msg)
					else:
						for buff_content in buff:
							msg_content = buff_content
							msg = msg_content.format(message)
							await client.send_message(diceboard, msg)

					msg_content = mathString
					msg = msg_content.format(message)
					await client.send_message(diceboard, msg)
					
					msg_content = "{0.author.mention} rolled: **" + str(result) + "**"
					msg = msg_content.format(message)
					await client.send_message(message.channel, msg)
					
				else:
					raise ValueError("diceroll failed")

			except:
				errorlog = traceback.format_exc()
				errorfmt = errorlog.splitlines()[-1]
				logger.error(errorfmt)
				print(errorlog)

				msg_content = ("{0.author.mention}\n```Usage: +roll [#]d[#] [+,-,x,/][#] . . .\n" +
								"       +roll 100000d10000 /2 +10 x100 -1```")
				msg = msg_content.format(message)
				await client.send_message(message.channel, msg)


		if message_content_lower.startswith('+masterbot'):
			msg_content = ("```MasterBot v0.1.2-beta:\n\n" +
							"Taking back control of a Discord Server near you!\n" +
							"Totally not part of a botnet tracking your every move... I swear! ;)```")
			msg = msg_content.format(message)
			await client.send_message(message.channel, msg)

		if message_content_lower.startswith('+starwars'):

			msg_content = "``` \n \n \n \n \n \n \n \n \n \n \n \n \n \nstarting█```"
			msg = msg_content.format(message)
			messagePrompt = await client.send_message(message.channel, msg)

			filename = "./bin/sw1.txt"

			with open(filename) as f:
				lines = f.readlines()

			frames = range(int(len(lines) / 14))

			await asyncio.sleep(1)
			for frame in frames:
				theframe = lines[(1 + (14 * frame)):(13 + (14 * frame))]
				framelen = int(int(lines[(0 + (14 * frame))]) / 12 + 1)
				framestr = ''
				for eachline in theframe:
					framestr = framestr + eachline

				for framecopy in range(framelen):
					msg_content = "``` \n" + framestr + "\n" + str(frame) + ":" + str(framecopy+1) + "```"
					msg = msg_content.format(message)
					await client.edit_message(messagePrompt, msg)
					await asyncio.sleep(0.1)

			msg_content = "``` \n \n \n \n \n \n \n \n \n \n \n \n \n \nend of file█```"
			msg = msg_content.format(message)
			await client.edit_message(messagePrompt, msg)

	except:
		errorlog = traceback.format_exc()
		errorfmt = errorlog.splitlines()[-1]
		logger.error(errorfmt)
		print(errorlog)

	if shut_down:
		await client.change_presence(game=discord.Game(name='ERROR'), status=discord.Status.do_not_disturb)
		await asyncio.sleep(0.1)
		await client.change_presence(game=discord.Game(name='shuting down'), status=discord.Status.do_not_disturb)
		await asyncio.sleep(0.1)
		await client.change_presence(game=discord.Game(name='ERROR'), status=discord.Status.do_not_disturb)
		await asyncio.sleep(0.1)
		await client.change_presence(game=discord.Game(name='u mutherfucker'), status=discord.Status.do_not_disturb)
		await asyncio.sleep(1)


		logger.error('shutdown')

		errorlog = traceback.format_exc()
		errorfmt = errorlog.splitlines()[-1]
		logger.debug(errorfmt)
		print(errorlog)

		await client.change_presence(game=discord.Game(name='dead'), status=discord.Status.invisible)

		sys.exit()

@client.event
async def on_ready():
	logMsg = 'MasterBot is now active'
	logger.warning(logMsg)

	activity = discord.Game(name=statusUpdate)
	await client.change_presence(status=statusMode, activity=activity)
	
	# await client.change_presence(game=discord.Game(name=statusUpdate), status=statusMode)

try:
	done = True
	logMsg = 'starting tasks...'
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
