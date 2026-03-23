#!/usr/bin/env python3
"""
WOPR v3 — Modern Discord Presence & Command Bot
"""

import os
import random
import logging
import asyncio
import traceback
from itertools import cycle

import discord
from discord.ext import commands, tasks


# =======================
# Logging Configuration
# =======================

LOG_PATH = "./wopr.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ],
)

logger = logging.getLogger("wopr")


# =======================
# Config
# =======================

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set.")

INTENTS = discord.Intents.default()
INTENTS.message_content = True  # required for reading message content


bot = commands.Bot(
    command_prefix=">",
    intents=INTENTS,
    help_command=None
)

# =======================
# Presence Rotation
# =======================

GAMES = [
    "Global Thermonuclear War",
    "Signal Analysis",
    "Strategic Simulation",
    "Cold Silence",
    "Stack Trace Review",
]

GAME_SUFFIXES = [
    "",
    " (idle)",
    " // awaiting input",
    " // observing",
    " // calculating",
]

@tasks.loop(seconds=600)
async def rotate_presence():
    game = random.choice(GAMES)
    suffix = random.choice(GAME_SUFFIXES)

    activity = discord.Game(name=f"{game}{suffix}")
    await bot.change_presence(activity=activity)

    logger.info(f"Presence changed to: {game}{suffix}")


# =======================
# Events
# =======================

@bot.event
async def on_ready():
    logger.info("WOPR ONLINE")
    print(f"Connected as {bot.user}")

    activity = discord.Game(name="Chapter One: Shall We Play A Game?")
    await bot.change_presence(activity=activity)

    if not rotate_presence.is_running():
        rotate_presence.start()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    logger.debug(f"[{message.guild}] {message.author}: {message.content}")

    await bot.process_commands(message)


# =======================
# Commands
# =======================

@bot.command(name="help")
async def help_command(ctx: commands.Context):
    msg = (
        "Available commands:\n"
        ">ping\n"
        ">status\n"
        ">roll NdM\n"
    )
    await ctx.send(msg)
    logger.info(f"Help requested by {ctx.author}")


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    latency = round(bot.latency * 1000)
    await ctx.send(f"Pong. {latency}ms")
    logger.info(f"Ping from {ctx.author}")


@bot.command(name="status")
async def status(ctx: commands.Context):
    await ctx.send("Systems nominal. Observing quietly.")
    logger.info(f"Status requested by {ctx.author}")


@bot.command(name="roll")
async def roll(ctx: commands.Context, dice: str):
    """
    Usage: >roll 2d6
    """
    try:
        rolls, limit = map(int, dice.lower().split("d"))
        if rolls > 100 or limit > 10000:
            raise ValueError

        results = [random.randint(1, limit) for _ in range(rolls)]
        total = sum(results)

        await ctx.send(f"🎲 {results} → Total: {total}")
        logger.info(f"Dice roll by {ctx.author}: {dice} → {total}")

    except Exception:
        await ctx.send("Invalid format. Use: >roll NdM (example: >roll 2d6)")
        logger.warning(f"Invalid roll attempt by {ctx.author}: {dice}")


# =======================
# Graceful Shutdown
# =======================

async def main():
    try:
        await bot.start(TOKEN)
    except KeyboardInterrupt:
        logger.warning("Shutdown requested by user.")
    except Exception:
        logger.error("Unexpected error.")
        logger.error(traceback.format_exc())
    finally:
        await bot.close()
        logger.warning("WOPR OFFLINE")


if __name__ == "__main__":
    asyncio.run(main())
