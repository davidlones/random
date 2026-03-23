#!/usr/bin/env python
# _0853RV3R
import urllib.request, urllib.error, urllib.parse, sys, subprocess, argparse, os, webbrowser, time, traceback
from bs4 import BeautifulSoup
import numpy as np
import matplotlib.pyplot as plt
from sklearn.linear_model import LinearRegression
from collections import Counter

def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

parser = argparse.ArgumentParser(description = \
"Lotto Numbers\n\
_0853RV3R",
formatter_class=argparse.RawTextHelpFormatter)

parser.add_argument('ipaddr', action="store", help="target")

year = 2021

year = str(year)

quote_page = "https://www.lottery.net/powerball/numbers/" + year

hdr = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.11 (KHTML, like Gecko) Chrome/23.0.1271.64 Safari/537.11',
       'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
       'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.3',
       'Accept-Encoding': 'none',
       'Accept-Language': 'en-US,en;q=0.8',
       'Connection': 'keep-alive'}

try:
	page = urllib.request.Request(quote_page, headers=hdr)
	data = urllib.request.urlopen(page)

	page = data.read()

	soup1 = BeautifulSoup(page, 'html.parser')

	rawballs = soup1.find_all('li', class_='ball')
	rawpowerballs = soup1.find_all('li', class_='powerball')

	balls = []
	for i in range(len(rawballs)):
	    balls.insert(0,int(rawballs[i].get_text().strip()))

	# print(balls)

	powerballs = []
	for i in range(len(rawpowerballs)):
	    powerballs.insert(0,int(rawpowerballs[i].get_text().strip()))

	# print(powerballs)

except:
	print('testing mode activated')
	balls = [2, 23, 18, 3, 21, 5, 37, 7, 1, 11]
	powerballs = [9, 1]


# X = np.array(range(len(balls))).reshape(-1,1)
# y = np.array(balls).reshape(-1,1)

# to_predict_x= [78, 79, 80, 81, 82]
# to_predict_x= np.array(to_predict_x).reshape(-1,1)

# regsr=LinearRegression()
# regsr.fit(X,y)

# predicted_y= regsr.predict(to_predict_x)
# m= regsr.coef_
# c= regsr.intercept_
# print("Predicted y:\n",predicted_y)
# print("slope (m): ",m)
# print("y-intercept (c): ",c)

def runningSums(lst):
    res = [lst[0]]
    for elem in lst[1:]:
        res.append(res[-1] + elem)
    return res
def antiRunningSums(lst):
    res = [lst[0]]
    for i in range(1,len(lst)):
        res.append(lst[i] - lst[i-1])
    return res
def predictnum(lst):
    deriv = 0
    while True:
        nxt = antiRunningSums(lst)
        if sum(map(abs, nxt)) > sum(map(abs, lst)):
            break
        lst = nxt
        deriv += 1
    lst.append(lst[-1])
    for i in range(deriv):
        lst = runningSums(lst)
    return lst

for i in range(0,5):
    balls.append(predictnum(balls)[-1])
    print(predictnum(balls)[-1])

print(predictnum(powerballs)[-1])