#!/usr/bin/env python
# _0853RV3R
import shelve, argparse

parser = argparse.ArgumentParser()

parser.add_argument('dbloc', action="store", help="database location")
parser.add_argument('dbadd', action="store", help="database addition")
argument = parser.parse_args()

database_file = argument.dbloc
database_add = argument.dbadd


dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)

dbfile[database_add] = {}

dbfile.close()