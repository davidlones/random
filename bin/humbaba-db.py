#!/usr/bin/env python
# _0853RV3R
import shelve, argparse, operator

parser = argparse.ArgumentParser()

parser.add_argument('db', action="store", help="database")
argument = parser.parse_args()

database_file = "/var/tmp/articles.dat"
database = argument.db


dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)

result = dbfile[database]
dbfile.close()

result_sorted = reversed(sorted(result.items(), key=operator.itemgetter(1)))

for response, attempts in result_sorted:
    print("=================================================")
    print(str(response))
    print("================================================= " + str(attempts) + "\n")