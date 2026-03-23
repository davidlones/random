#!/usr/bin/env python
# _0853RV3R
import shelve, argparse, operator

parser = argparse.ArgumentParser()

parser.add_argument('dbloc', action='store', help='database location')
parser.add_argument('-c', '--create', action='store', dest='dbcreate', help='database addition')
parser.add_argument('-n', '--humbaba', action="store_true", dest="dbhumbaba", help="default file for Humbaba")
parser.add_argument('-t', '--touch', action="store_true", dest="dbtouch", help="touch file")
parser.add_argument('-r', '--read', action="store", dest="dbread", help="read database")
parser.add_argument('-l', '--load', action="store", dest="dbload", help="load database")
parser.add_argument('-d', '--del', action='store', dest='dbdel', help='item deletion')
parser.add_argument('-i', '--item', action='store', dest='dbadd', help='item addition')
parser.add_argument('-k', '--key', action='store', dest='dbkey', help='item key addition')

argument = parser.parse_args()

database_file = argument.dbloc
database_create = argument.dbcreate
database_humbaba = argument.dbhumbaba
database_touch = argument.dbtouch
database_read = argument.dbread
database_load = argument.dbload
item_del = argument.dbdel
item_add = argument.dbadd
item_key = argument.dbkey


dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)

if database_read:
	db = dbfile[database_read]
	db_content = sorted(db.items(), key=operator.itemgetter(1))
	for item, key in db_content:
	    print('Item: [ ' + str(item) + ' ] \nKey: [ ' + str(key) + ' ] \n')

if database_humbaba:
	dbfile['ipaddresses'] = {}
	dbfile['ipblacklist'] = {}
	dbfile['data'] = {}
	dbfile['cedar'] = {}
	dbfile['keys'] = {}
	dbfile['keyblacklist'] = {}

if database_create:
	dbfile[database_create] = {}

if database_load:
	dictionary = dbfile[database_load]
	if item_del:
		del dictionary[item_del]

	if item_add:d
		dictionary[item_add] = item_key

	dbfile[database_load] = dictionary

dbfile.close()