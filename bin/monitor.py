#!/usr/bin/env python
# _0853RV3R
from __future__ import print_function
from Adafruit_LED_Backpack import SevenSegment
import time, subprocess, sys, os, threading, logging, requests, traceback

logger = logging.getLogger(__name__)
hdlr = logging.FileHandler('/var/tmp/monitor.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)

# logger = logging.getLogger(__name__)
# hdlr = logging.FileHandler('/var/tmp/monitor.log')
# formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
# hdlr.setFormatter(formatter)

# errhdlr = logging.StreamHandler()
# errhdlr.setFormatter(formatter)

# logger.addHandler(hdlr)
# logger.setLevel(logging.DEBUG)
# logger.addHandler(errhdlr)
# errhdlr.setLevel(logging.WARNING)

def notification(data1=None,data2=None,data3=None):
    report = {}
    report["value1"] = data1
    report["value2"] = data2
    report["value3"] = data3
    requests.post("https://maker.ifttt.com/trigger/notification/with/key/fC5hSqmZaDri-BAfNpT27rLAaRfiFGjBSW-6E5WE4oM", data=report)

def notetime():
    return time.strftime("%I:%M:%S %p")

def gettemp():
    HOST="davidlones@10.0.1.201"
    COMMAND="/Applications/TemperatureMonitor.app/Contents/MacOS/tempmonitor -c -l -a | grep 'SMC CPU A DIODE' | tail -c 5 | head -c 2"
    ssht = subprocess.Popen(["ssh", HOST, "echo -n"], shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sshtest = ssht.stdout.read()
    print (sshtest, end="")
    ssh = subprocess.Popen(["ssh", HOST, COMMAND], shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    temp1 = ssh.stdout.read()
    temp2 = int(''.join(['0x', temp1, 'C']), 16)

    return temp1, temp2

class flashdisplay(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

    def run(self):
        self.cont = True
        while self.cont == True:
            display.clear()
            display.print_float(float(-100.1), decimal_digits=1, justify_right=True)
            display.write_display()
            time.sleep(0.2)
            display.clear()
            display.write_display()
            time.sleep(0.2)

    def stop(self):
        self.cont = False

def fan(do):
    if do == 1:
        subprocess.Popen(["/home/_08server/.bin/wemo", "10.0.1.31", "on"], shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if do == 0:
        subprocess.Popen(["/home/_08server/.bin/wemo", "10.0.1.31", "off"], shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


logger.warning('SYSTEM MONITOR STARTED')
print('SYSTEM MONITOR STARTED')
notification('SYSTEM MONITOR STARTED')

display = SevenSegment.SevenSegment(address=0x70, busnum=1)
colon = False
display.begin()

try:
    try:
        while True:
            fd = flashdisplay()
            fd.start()
            temp, temp_str = gettemp()

            logger.info('Displaying Temperature [' + temp + 'C]')
    #       logger.debug('[' + str(temp_str) + ']')

            try:
                if temp == "":
                    fan(1)
                    logger.error('Connection Failed!')
                    notification('Connection Failed!')

                    result = False

                elif int(temp) > 64:
                    fan(1)
                    logger.warning('Temperature Warning!')
                    notification('Temperature Warning!',temp + ' Degrees')

                    result = temp_str

                elif int(temp) < 57:
                    fan(0)
                    result = temp_str

            except Exception:
                result = False
                sys.exc_clear()

            fd.stop()

            time.sleep(0.3)
            display.clear()
            display.write_display()
            
            if result:
                display.print_hex(result)
            else:
                display.clear()
                display.print_float(float(-100.1), decimal_digits=1, justify_right=True)

            display.write_display()
            time.sleep(15)

            hits = subprocess.Popen(["cat", "/var/log/www/count"], shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            hitcount = hits.stdout.read()
            hit_str = int(''.join(['0x', hitcount, 'd']), 16)
            logger.info('Displaying Counter [' + hitcount + 'd]')
    #       logger.debug('[' + str(hit_str) + ']')

            time.sleep(0.3)
            display.clear()
            display.write_display()
            display.print_hex(hit_str)
            display.write_display()
            time.sleep(8)

            logger.info('Displaying Time')
    #       logger.debug('[' + time.strftime("%I%M") + ']')
            for i in range(240):
                thetime = time.strftime("%I%M")
                colon = not colon
                display.clear()
                display.print_float(float(thetime), decimal_digits=0, justify_right=True)
                display.set_colon(colon)
                display.write_display()
                time.sleep(0.5)

    except KeyboardInterrupt:
        logger.error('User Exited')
        notification('User Exited')
        errorlog = traceback.format_exc()
        errorfmt = errorlog.splitlines()[-1]
        logger.debug(errorfmt)
        print(errorfmt)
        exit()

    except Exception:
        logger.error('Wiring Fault?')
        notification('Wiring Fault?')
        errorlog = traceback.format_exc()
        errorfmt = errorlog.splitlines()[-1]
        logger.debug(errorfmt)
        print(errorlog)
        time.sleep(300)

finally:
    logger.warning("SYSTEM MONITOR STOPPED")
    print("SYSTEM MONITOR STOPPED")
    notification('SYSTEM MONITOR STOPPED')

print ("Display Failed?")
logger.warning('Display Failed?')
notification('Display Failed?')
