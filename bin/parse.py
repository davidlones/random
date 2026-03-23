#!/usr/bin/env python
# _0853RV3R
import subprocess, logging, socket, sys, time, os, shelve, traceback, errno, pyotp, argparse
from thread import *
import urllib2, urllib
from M2Crypto import BIO, RSA, Rand
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer

parser = argparse.ArgumentParser(description = \
"\n\
_0853RV3R",
formatter_class=argparse.RawTextHelpFormatter)

parser.add_argument('-s', '--server', action="store_true", dest="server", help="server mde")
parser.add_argument('-c', '--client', action="store_true", dest="client", help="client mode")

if len(sys.argv[1:])==0:
	parser.print_help()		
	parser.exit()

argument = parser.parse_args()

server = argument.server
client = argument.client

if server:
	print("server mode")

if client:
	print("client mode")