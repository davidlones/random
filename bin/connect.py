#!/usr/bin/env python
# _0853RV3R
import pyotp, time

httpsecret = 'thisisthelastone'
httptotp = pyotp.TOTP(httpsecret)

commsecret = 'butwhynotfirsttw'
commtotp = pyotp.TOTP(commsecret)

while True:
	print(commtotp.now().replace('0', '1'))
	print(httptotp.now())
	time.sleep(30)