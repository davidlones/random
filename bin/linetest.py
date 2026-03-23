#!/usr/bin/env python
# _0853RV3R
import requests
ifttt_webhook = "https://maker.ifttt.com/trigger/notification/with/key/fC5hSqmZaDri-BAfNpT27rLAaRfiFGjBSW-6E5WE4oM"

def notification(data1=None,data2=None,data3=None):
    report = {}
    report["value1"] = data1
    report["value2"] = data2
    report["value3"] = data3

    requests.post(ifttt_webhook,data=report)

notification('hello testing')
print("done")