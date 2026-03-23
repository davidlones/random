import socket
import math
import time

filename = "/home/davidlones/Downloads/sw1.txt"

print("``` \n \n \n \n \n \n \n \n \n \n \n \n \n \nstarting█```")
# msg = msg_content.format(message)
# messagePrompt = await client.send_message(message.channel, msg)

with open(filename) as f:
    lines = f.readlines()

frames = range(int(len(lines) / 14))

time.sleep(1)
for frame in frames:
    theframe = lines[(1 + (14 * frame)):(13 + (14 * frame))]
    framelen = int(int(lines[(0 + (14 * frame))]) / 12 + 1)
    framestr = ''
    for eachline in theframe:
        framestr = framestr + eachline

    for framecopy in range(framelen):
        print("``` \n" + framestr + "\n" + str(frame) + ":" + str(framecopy+1) + "```")
        # msg = msg_content.format(message)
        # await client.edit_message(messagePrompt, msg)
        time.sleep(0.1)

# msg_content = "``` \n \n \n \n \n \n \n \n \n \n \n \n \n \nend of file█```"
# msg = msg_content.format(message)
# await client.edit_message(messagePrompt, msg)
