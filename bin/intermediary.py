#!/usr/bin/env python
# _0853RV3R
import subprocess, logging, socket, sys, time, os, requests, shelve, traceback, pyotp, errno
from thread import *

logger = logging.getLogger(__name__)
hdlr = logging.FileHandler('/var/tmp/system.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)

database_file = "/var/tmp/system.dat"

AbuseIPDB = "/home/_08server/.bin/abuseipdb"
report_dir = "/var/tmp/humbaba-reports/"

keyfile1 = "/home/bob/.ssh/authorized_keys"
keyfile2 = "/home/_08server/.ssh/authorized_keys"
contfile = "/var/tmp/1"

HOST = '0.0.0.0'
PORT = 59214
RECV_BUFFER = 1024

secret = "thiswillnotlastt"
attempt_limit = 10
timeout = 7

ifttt_webhook = "https://maker.ifttt.com/trigger/notification/with/key/fC5hSqmZaDri-BAfNpT27rLAaRfiFGjBSW-6E5WE4oM"

def notification(data1=None,data2=None,data3=None):
    report = {}
    report["value1"] = data1
    report["value2"] = data2
    report["value3"] = data3

    requests.post(ifttt_webhook,data=report)

def notetime():
    return time.strftime("%I:%M:%S %p")

def noteaddr():
    return addr[0] + ":" + str(addr[1])

def IPcheck():
    user = addr[0]
    dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
    ipaddresses = dbfile["ipaddresses"]
    ipblacklist = dbfile["ipblacklist"]

    if user not in ipaddresses:
        ipaddresses[user] = 0
        logger.warning("[" + noteaddr() + "] NEW ADDRESS")
        FNULL = open(os.devnull, 'w')
        subprocess.Popen([AbuseIPDB, user, "-n", "-o", report_dir + user, "-x"], stdout=FNULL, stderr=subprocess.STDOUT)

    ipaddresses[user] += 1

    attempts = ipaddresses[user]

    logger.warning("[" + noteaddr() + "] ATTEMPT [" + str(attempts) + "]")

    if attempts > attempt_limit and user not in ipblacklist:
        ipblacklist[user] = 0

    if user in ipblacklist:
        logger.warning("[" + noteaddr() + "] INTERMEDIARY DENIED CONNECTION")
        ipblacklist[user] += 1
        logger.warning("[" + noteaddr() + "] INTERMEDIARY BLACKLISTED [" + str(ipblacklist[user]) + "]")
        blacklisted = True

    else:
        logger.warning("[" + noteaddr() + "] INTERMEDIARY ACCEPTED CONNECTION")
        notification('INTERMEDIARY ACCEPTED CONNECTION',"[" + noteaddr() + "]")
        blacklisted = False

    dbfile["ipaddresses"] = ipaddresses
    dbfile["ipblacklist"] = ipblacklist
    dbfile.close()

    return blacklisted, attempts

def logdata(data):
    dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
    datadb = dbfile["data"]

    if data not in datadb:
        datadb[data] = 0
    datadb[data] += 1

    dbfile["data"] = datadb
    dbfile.close()

    logger.info("[" + noteaddr() + "] [" + data + "]")


def writekey(keyblock):
    with open(keyfile1, "w") as f:
        f.write(keyblock)

def revoke():
    with open(keyfile1, "w") as f:
        f.write("")
    subprocess.Popen(["pkill", "-9", "-u", "bob"])

def savekey(keyblock):
    with open(keyfile2, "a") as f:
        thefile = open(keyfile2, "r").read()
        if keyblock not in thefile:
            f.write("\n" + keyblock)
            saved = True
        else:
            saved = False

    return saved

def keycheck(keyreceived):
    user = addr[0]
    dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
    keys = dbfile["keys"]
    keyblacklist = dbfile["keyblacklist"]
    ipaddresses = dbfile["ipaddresses"]
    ipblacklist = dbfile["ipblacklist"]


    if keyreceived not in keys:
        keys[keyreceived] = user
    elif keys[keyreceived] is not user and keyreceived not in keyblacklist:
        keyblacklist[keyreceived] = 0
        logger.warning("[" + noteaddr() + "] INTERMEDIARY KEY ALREADY EXISTS, ADDRESS DOES NOT MATCH")

    logger.info("[" + noteaddr() + "] [" + keyreceived + "]")

    attempts = ipaddresses[user]

    logger.warning("[" + noteaddr() + "] ATTEMPT [" + str(attempts) + "]")

    if keyreceived in keyblacklist:
    	if user not in ipblacklist:
            ipblacklist[user] = 0
        ipblacklist[user] += 1
        keyblacklist[keyreceived] += 1

        logger.warning("[" + noteaddr() + "] INTERMEDIARY DENIED CONNECTION")
        logger.warning("[" + noteaddr() + "] KEY BLACKLISTED [" + str(keyblacklist[keyreceived]) + "]")
        notification('INTERMEDIARY BLACKLISTED KEY',"[" + noteaddr() + "]")
        blacklisted = True

    else:
        logger.warning("[" + noteaddr() + "] INTERMEDIARY ACCEPTED KEY")
        notification('INTERMEDIARY ACCEPTED KEY',"[" + noteaddr() + "]")
        blacklisted = False

    dbfile["keys"] = keys
    dbfile["keyblacklist"] = keyblacklist
    dbfile["ipblacklist"] = ipblacklist
    dbfile.close()

    return blacklisted, attempts

# def keycheck(keyreceived):
#     dbfile = shelve.open(database_file, flag='c', protocol=None, writeback=False)
#     keys = dbfile["keys"]
#     keyblacklist = dbfile["keyblacklist"]

#     if keyreceived not in keys:
#         keys[datalog] = 0
#     keys[datalog] += 1

#     dbfile["keys"] = keys
#     dbfile.close()

#     logger.info("[" + noteaddr() + "] [" + keyreceived + "]")


def ParsePacket(packet):
    packetDecoded = packet.decode('base64')
    packetParsed = packetDecoded.split(":")

    passcode =packetP packetParsed[0].decode('base64')
    key = packetParsed[1].decode('base64')

    return passcode, key

def cleanup():
    os.remove(contfile)
    with open("/home/bob/.bash_history", "w") as f:
        f.write("")

class AccessDenied(Exception):
    pass

def clientthread(conn, auth):
    try:
        blacklisted, attempts = IPcheck()
        if not blacklisted:
            data = conn.recv(RECV_BUFFER)
            datalog = data.rstrip("\r\n")

            if data:
                logdata(datalog)

                passcode, key = ParsePacket(datalog)
                authpassed = auth.verify(passcode)
                print(authpassed)

            if authpassed:
                if key:
                    print(key)
                    keyblacklisted, attempts = keycheck(key)
                    if keyblacklisted:
                        raise AccessDenied
                    else:
                        writekey(key)
                        logger.warning("[" + noteaddr() + "] INTERMEDIARY GRANTED ACCESS")
                        notification('INTERMEDIARY GRANTED ACCESS',"[" + noteaddr() + "]")

                        time.sleep(timeout)
                        failsafe = os.path.isfile(contfile)
                        if failsafe:
                            revoke()
                            saved = savekey(key)
                            if saved:
                                logger.warning("[" + noteaddr() + "] INTERMEDIARY KEY SAVED")
                                notification('KEY SAVED',"[" + noteaddr() + "]")
                            else:
                                logger.warning("[" + noteaddr() + "] INTERMEDIARY KEY ALREADY EXISTS?")
                                notification('KEY ALREADY EXISTS?',"[" + noteaddr() + "]")
                            cleanup()
                        else:
                            logger.warning("[" + noteaddr() + "] INTERMEDIARY REVOKED ACCESS")
                            notification('INTERMEDIARY REVOKED ACCESS',"[" + noteaddr() + "]")
                            revoke()

            else:
                raise AccessDenied


    except AccessDenied:
        logger.warning("[" + noteaddr() + "] INTERMEDIARY DENIED ACCESS")
        notification('INTERMEDIARY DENIED ACCESS',"[" + noteaddr() + "]")


    except:
        logger.error("[" + noteaddr() + "] CONNECTION FAILED")

        errorlog = traceback.format_exc()
        errorfmt = errorlog.splitlines()[-1]
        logger.debug("[" + noteaddr() + "] " + errorfmt)
        print(errorlog)

    finally:
        conn.close( )


###### SERVER INIT: ######
try:
    logger.warning('INTERMEDIARY SERVER HAS STARTED')
    print("SERVER HAS STARTED")

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((HOST, PORT))
    s.listen(10)

    auth = pyotp.TOTP(secret)

    logger.debug("INTERMEDIARY: Now listening [" + HOST + ":" + str(PORT) + "]")
    logger.debug("INTERMEDIARY: Attempt Limit: [" + str(attempt_limit) + "]")
    logger.debug("INTERMEDIARY: Timeout: [" + str(timeout) + "s]")

    notification('INTERMEDIARY ACTIVE')

    while True:
        authcode = "test"
        conn, addr = s.accept()
        start_new_thread(clientthread ,(conn, auth))

except KeyboardInterrupt:
    logger.error('User Exited')

    errorlog = traceback.format_exc()
    errorfmt = errorlog.splitlines()[-1]
    logger.debug(errorfmt)
    print(errorfmt)

except socket.error:
    logger.error('Bind Failed')

    errorlog = traceback.format_exc()
    errorfmt = errorlog.splitlines()[-1]
    logger.debug(errorfmt)
    print(errorfmt)

except:
    logger.error('Oops?')

    errorlog = traceback.format_exc()
    errorfmt = errorlog.splitlines()[-1]
    logger.debug(errorfmt)
    print(errorlog)

finally:
    logger.warning("INTERMEDIARY SERVER HAS STOPPED")
    print("SERVER HAS STOPPED")
    notification('INTERMEDIARY INACTIVE')

    s.close()
