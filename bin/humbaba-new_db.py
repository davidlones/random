#!/usr/bin/env python
# _0853RV3R
import shelve, argparse

parser = argparse.ArgumentParser()

parser.add_argument('dbloc', action='store', help='database location')
parser.add_argument('-a', '--add', action='store', dest='dbadd', help='database addition')
parser.add_argument('-n', '--new', action='store_true', dest='dbnew', help='create new default system database')
parser.add_argument('-t', '--touch', action="store_true", dest="dbtouch", help="touch file")
argument = parser.parse_args()

database_file = argument.dbloc
database_add = argument.dbadd
database_new = argument.dbnew
database_touch = argument.dbtouch


dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)

if database_new:
	dbfile['ipaddresses'] = {}
	dbfile['ipblacklist'] = {}
	dbfile['data'] = {}
	dbfile['cedar'] = {}
	dbfile['keys'] = {}
	dbfile['keyblacklist'] = {}

if database_add:
	dbfile[database_add] = {}

dbfile.close()