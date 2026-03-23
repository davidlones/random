import time

mytime = '8:36 AM'

def thetime(t):
	if t in time.strftime("%I:%M %p"):
		return True
	else:
		return False

while True:
	if thetime(mytime):
		print('its ' + mytime)
	else:
		print('not yet, its ' + time.strftime("%I:%M %p"))
	time.sleep(60)