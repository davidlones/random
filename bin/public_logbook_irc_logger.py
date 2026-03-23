#!/usr/bin/env python3
from __future__ import annotations

import socket
import time

from logbook_irc_common import (
    ALLOWED_CHANNELS,
    IRC_HOST,
    IRC_PORT,
    append_message,
    channel_name,
    clean_channel,
    clean_message,
)


BOT_NICK = "logbookd"


def parse_privmsg(line: str) -> tuple[str, str, str] | None:
    if " PRIVMSG " not in line or not line.startswith(":"):
        return None
    prefix, rest = line[1:].split(" ", 1)
    nick = prefix.split("!", 1)[0]
    try:
        _, channel, message = rest.split(" ", 2)
    except ValueError:
        return None
    if not message.startswith(":"):
        return None
    return nick, clean_channel(channel), clean_message(message[1:])


def run_once() -> None:
    sock = socket.create_connection((IRC_HOST, IRC_PORT), timeout=10)
    # Stay connected during idle periods; IRC can be quiet for longer than a minute.
    sock.settimeout(None)
    reader = sock.makefile("r", encoding="utf-8", errors="ignore", newline="\r\n")
    writer = sock.makefile("w", encoding="utf-8", newline="\r\n")

    def send(line: str) -> None:
        writer.write(line + "\r\n")
        writer.flush()

    send(f"NICK {BOT_NICK}")
    send(f"USER {BOT_NICK} 0 * :Sol-37 IRC logger")

    registered = False
    while not registered:
        line = reader.readline()
        if not line:
            raise RuntimeError("irc_closed")
        line = line.rstrip("\r\n")
        if line.startswith("PING "):
            send("PONG " + line.split(" ", 1)[1])
            continue
        if " 001 " in line or " 422 " in line or " 376 " in line:
            registered = True

    for chan in ALLOWED_CHANNELS:
        send(f"JOIN {channel_name(chan)}")

    while True:
        line = reader.readline()
        if not line:
            raise RuntimeError("irc_closed")
        line = line.rstrip("\r\n")
        if line.startswith("PING "):
            send("PONG " + line.split(" ", 1)[1])
            continue
        parsed = parse_privmsg(line)
        if not parsed:
            continue
        nick, chan, message = parsed
        if nick == BOT_NICK:
            continue
        append_message(nick, message, chan, source="irc")


def main() -> None:
    while True:
        try:
            run_once()
        except Exception as exc:
            print(f"irc logger reconnect after error: {exc}", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
