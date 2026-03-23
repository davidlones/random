#!/usr/bin/python3
import random, math, sys

theString = sys.argv[1]
diceStringParsed = theString.split('d')

if len(sys.argv) > 2:
	mathString1 = sys.argv[2].translate(str.maketrans({"x": r"*", "X": r"*"}))

	if len(sys.argv) > 3:
		mathString2 = sys.argv[3].translate(str.maketrans({"x": r"*", "X": r"*"}))
	else:
		mathString2 = False
else:
	mathString1 = False

try:
	dice = int(diceStringParsed[0])
except ValueError:
	dice = 1

sides = int(diceStringParsed[1])

results = []

if dice:
	while dice > 0:
		roll = random.randint(1,sides)
		results.append(roll)
		dice = dice - 1
else:
	roll = random.randint(1,sides)
	results.append(roll)

result = sum(results)

print(results)

if mathString1:
	mathResult1 = eval(str(result) + mathString1)

	if mathString2:
		mathResult2 = eval(str(mathResult1) + mathString2)
		print('(' + str(result) + mathString1 + ')' + mathString2)
		print(mathResult2)

	else:
		print(str(result) + mathString1)
		print(mathResult1)

else:
	print(result)