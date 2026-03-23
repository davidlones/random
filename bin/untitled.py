import feedparser, discord, random, logging, asyncio, shelve, traceback, sys, operator

random_sample = random.sample(range(21), 4)

channel = {}
channel[1] = 'one'
channel[2] = 'two'
channel[3] = 'three'
channel[4] = 'four'

x = 1
for article_number in random_sample:
	print(article_number)
	print(channel[x])
	x += 1