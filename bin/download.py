#!/usr/bin/env python
# _0853RV3R
import subprocess, logging, socket, sys, time, os, requests, shelve, traceback, pyotp, errno
from thread import *

parser = argparse.ArgumentParser(description = \
"AbuseIPDB Checker v1.2\n\
\n\
Checks AbuseIPDB.com for reports on an IP address.\n\
\n\
anti-hacker toolkit\n\
_0853RV3R",
formatter_class=argparse.RawTextHelpFormatter)

parser.add_argument('source', action="store", help="target source address")
parser.add_argument('-o', '--output', action="store", dest="outputfile", help="output file location")

if len(sys.argv[1:])==0:
    parser.print_help()        
    parser.exit()

argument = parser.parse_args()

source = argument.source
outputfile = argument.outputfile


def downloadthread(source, outputfile):
    



try:

    while True:
        start_new_thread(clientthread ,(source, outputfile))

        source = = raw_input("")
        outputfile = source.rsplit('/', 1)[-1]
        outputfile = raw_input("Save File to: ~/Downloads/" + outputfile)


finally:
    
