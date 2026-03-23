import time, subprocess, requests, ast
from datetime import datetime

ifttt_webhook = "https://maker.ifttt.com/trigger/notification/with/key/fC5hSqmZaDri-BAfNpT27rLAaRfiFGjBSW-6E5WE4oM"
abuseipdb_key = "R8Waams0meIVSdm20VSv33RQciHz9WW0k4EpflI1"

def notetime():
	return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

def notification(data1=None,data2=None,data3=None):
    report = {}
    report["value1"] = data1
    report["value2"] = data2
    report["value3"] = data3

    requests.post(ifttt_webhook,data=report)

ipaddr = "59.111.30.195"


def get_cat(x):
    return {
        3: 'Frad_Orders',
        4: 'DDoS_Attack',
        9: 'Open_Proxy',
        10: 'Web_Spam',
        11: 'Email_Spam',
        14: 'Port_Scan',
        18: 'Brute_Force',
        19: 'Bad_Web_Bot',
        20: 'Exploited_Host',
        21: 'Web_App_Attack',
        22: 'SSH',
        23: 'IoT_Targeted',
    }.get(x)


def get_report(IP, key):
    request = "https://www.abuseipdb.com/check/" + ipaddr + "/json?key=" + abuseipdb_key + "&days=90"
    # DEBUG
    # print(request)
    r = requests.get(request)
    # DEBUG
    print(r.json())
    try:
        data = r.json()
        if data == []:
            print("%s:  No Abuse Reports" % IP)
        else:
            for record in data:
                log = []
                ip_address = "" #("Alert for %s:" % IP)
                # ip_address = record['ip']
                country = record['country']
                # iso_code = record['isoCode']
                category = record['category']
                created = record['created']
                log.append(ip_address)
                log.append(country)
                # log.append(iso_code)
                log.append(created)
                for cat in category:
                    temp_cat = get_cat(cat)
                    log.append(temp_cat)
                    print('\t'.join(map(str, log)))
                    log.remove(temp_cat)
    except (ValueError, KeyError, TypeError):
        print("JSON format error")



get_report(ipaddr, abuseipdb_key)



# results = "ERROR 522"

# while True:
#     loadtest = subprocess.Popen(["wget", "-p", "abuseipdb.com", "-O", "/dev/null"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#     results = loadtest.stderr.read()

#     if "ERROR 522" not in results:
#         print(notetime() + " AbuseIPDB is back up!")
#         notification("AbuseIPDB is back up!")
#         print("\n" + results + "\n")
#     else:
#         print(notetime() + " AbuseIPDB.com is still down.")

#     time.sleep(3600)
