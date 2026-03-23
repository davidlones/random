#!/usr/bin/python
from __future__ import print_function
import time, threading


def startstop(usr):
 cond = usr
 global cond
 if cond == 1:
  class theprocess(threading.Thread):
   def run(self):
    while cond == 1:
     print(cond)
     time.sleep(1)
  ss = theprocess ()
  ss.daemon = True
  ss.start()

startstop(1)
time.sleep(5)
startstop(0)
print('stopped')

time.sleep(3)

print()

class starterstoper:
 def start():
  global cond
  cond = True
  class theprocess(threading.Thread):
   def run(self):
    while cond:
     print(cond)
     time.sleep(1)
  ss = theprocess ()
  ss.daemon = True
 def stop():
  global cond
  cond = False
startstop = starterstoper()

startstop.start()
time.sleep(5)
startstop.stop()
print('stopped')

time.sleep(3)

