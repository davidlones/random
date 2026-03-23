#!/usr/bin/python
from AppKit import NSApplication, NSApp
from Foundation import NSObject, NSLog
from Cocoa import NSEvent, NSKeyDownMask
from PyObjCTools import AppHelper
import threading, sys, time


class AppDelegate(NSObject):
 def applicationDidFinishLaunching_(self, notification):
  mask = NSKeyDownMask
  NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, handler)


def handler(event):
 try:
  NSLog(u"%@", event)
 except KeyboardInterrupt:
  AppHelper.stopEventLoop()


def main():
 app = NSApplication.sharedApplication()
 delegate = AppDelegate.alloc().init()
 NSApp().setDelegate_(delegate)
 AppHelper.runEventLoop()

#if __name__ == '__main__':

thelog = ""

class logger(threading.Thread):
 def run(self):
  global thelog
  thelog = main()
lg = logger()
lg.daemon = True
lg.start()

while True:
 print "hello"
 print thelog
 print "hello again"
 print thelog