import asyncio
from kasa import SmartBulb
import random
import time

p = SmartBulb("10.0.1.3")

async def sunrise():
    await p.set_color_temp(2500)
    await p.set_color_temp(6500, transition=10000)

async def all_colors():
    while True:
        hue = random.randint(0,359)
        sat = random.randint(0,100)
        await p.set_hsv(hue, sat, 100)
        time.sleep(0.5)

async def main():
    await p.update()
    await p.turn_on()
    await sunrise()



if __name__ == "__main__":
    asyncio.run(main())