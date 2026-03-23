#!/usr/bin/env python
import requests, time, sys

def notification(data1=None,data2=None,data3=None):
  report = {}
  report["value1"] = data1
  report["value2"] = data2
  report["value3"] = data3

  requests.post("https://maker.ifttt.com/trigger/notification/with/key/fC5hSqmZaDri-BAfNpT27rLAaRfiFGjBSW-6E5WE4oM",data=report)

notification(sys.argv[1])
