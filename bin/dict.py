import shelve

IPaddr = "74.221.101.29"

database_file = "/var/tmp/humbaba.dat"
dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)


IPdatabase = dbfile["IPaddresses"]

del IPdatabase[IPaddr]

dbfile["IPaddresses"] = IPdatabase
dbfile.close()



# print("\n\n\n")

# dbfile = shelve.open('/Users/davidlones/Public/drop/humbaba.dat', flag='c', protocol=None, writeback=False)
# IPdatabase = dbfile["IPaddresses"]

# for line in IPaddrs:
#     line = line.rstrip("\r\n")
#     if line not in IPdatabase:
#         print("not found")
#     else:
#         print(line + ": " + str(IPdatabase[line]))














# if IPaddr in IPdatabase:
#     print("found")
#     initcount = IPdatabase[IPaddr]
#     IPdatabase[IPaddr] = initcount + 1
# else:
#     print("not found")
    

# dbfile["IPaddresses"] = IPdatabase

# postcount = IPdatabase[IPaddr]
# dbfile.close()

# print(initcount)
# print(postcount)