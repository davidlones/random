#!/usr/bin/env python3
from __future__ import annotations

import json
import secrets
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATA_DIR = Path("/home/david/random/data/logbook")
REAL_LOG_PATH = DATA_DIR / "messages.jsonl"
SYNTH_LOG_PATH = DATA_DIR / "synthetic_messages.jsonl"
IRC_HOST = "127.0.0.1"
IRC_PORT = 6667
IRC_REALNAME = "Sol-37 Public Logbook"
ALLOWED_CHANNELS = ("public-logbook", "archive-watch", "civilization-sim")
MAX_NAME_LEN = 24
MAX_MESSAGE_LEN = 400
MAX_CHANNEL_LEN = 32
DEDUP_WINDOW_SECONDS = 8


def ensure_store() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REAL_LOG_PATH.touch(exist_ok=True)
    SYNTH_LOG_PATH.touch(exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def collapse_ws(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def clean_name(text: Any) -> str:
    value = collapse_ws(str(text or ""))[:MAX_NAME_LEN]
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isprintable())


def clean_nick(text: Any) -> str:
    raw = clean_name(text).replace(" ", "_")
    filtered = "".join(ch for ch in raw if ch.isalnum() or ch in "-_[]\\`^{}|")
    if not filtered:
        filtered = f"guest{secrets.token_hex(2)}"
    if not (filtered[0].isalpha() or filtered[0] in "[]\\`^{}|_"):
        filtered = f"u{filtered}"
    return filtered[:MAX_NAME_LEN]


def clean_message(text: Any) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in value.split("\n")]
    value = "\n".join(line for line in lines if line)[:MAX_MESSAGE_LEN]
    return "".join(ch for ch in value if ch.isprintable() or ch == "\n")


def irc_payload_message(text: str) -> str:
    return text.replace("\n", " // ")


def clean_channel(text: Any) -> str:
    value = collapse_ws(str(text or ""))[:MAX_CHANNEL_LEN].lower()
    value = "".join(ch for ch in value if ch.isalnum() or ch in "-_#")
    value = value.lstrip("#")
    return value if value in ALLOWED_CHANNELS else "public-logbook"


def channel_name(channel: str) -> str:
    return f"#{clean_channel(channel)}"


def parse_iso(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def recent_messages(limit: int, channel: str | None = None) -> list[dict[str, Any]]:
    ensure_store()
    rows: list[dict[str, Any]] = []
    source_rows = _read_rows(REAL_LOG_PATH) + _read_rows(SYNTH_LOG_PATH)
    source_rows.sort(key=lambda row: row.get("created_at", ""))
    for row in source_rows[-max(limit * 6, 80):]:
        row_channel = clean_channel(row.get("channel"))
        if channel and row_channel != clean_channel(channel):
            continue
        rows.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "message": row.get("message"),
                "created_at": row.get("created_at"),
                "channel": row_channel,
            }
        )
    return rows[-limit:]


def append_message(name: str, message: str, channel: str, source: str = "api") -> dict[str, Any]:
    ensure_store()
    row = {
        "id": f"{int(time.time() * 1000)}-{secrets.token_hex(3)}",
        "name": clean_name(name),
        "message": clean_message(message),
        "channel": clean_channel(channel),
        "created_at": now_iso(),
        "source": source,
    }

    existing = recent_messages(12, row["channel"])
    row_dt = parse_iso(row["created_at"])
    for prev in reversed(existing):
        if not prev.get("created_at"):
            continue
        try:
            prev_dt = parse_iso(prev["created_at"])
        except Exception:
            continue
        if abs((row_dt - prev_dt).total_seconds()) > DEDUP_WINDOW_SECONDS:
            continue
        if prev.get("name") == row["name"] and prev.get("message") == row["message"] and clean_channel(prev.get("channel")) == row["channel"]:
            return {
                "id": prev.get("id"),
                "name": prev.get("name"),
                "message": prev.get("message"),
                "channel": clean_channel(prev.get("channel")),
                "created_at": prev.get("created_at"),
                "source": prev.get("source", source),
            }

    with REAL_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    return row


class IRCError(RuntimeError):
    pass


def send_irc_message(nick: str, channel: str, message: str, timeout: float = 8.0) -> None:
    target_channel = channel_name(channel)
    target_nick = clean_nick(nick)
    payload = irc_payload_message(clean_message(message))
    if not payload:
        raise IRCError("empty_message")

    sock = socket.create_connection((IRC_HOST, IRC_PORT), timeout=timeout)
    sock.settimeout(timeout)
    reader = sock.makefile("r", encoding="utf-8", errors="ignore", newline="\r\n")
    writer = sock.makefile("w", encoding="utf-8", newline="\r\n")

    def send(line: str) -> None:
      writer.write(line + "\r\n")
      writer.flush()

    try:
        send(f"NICK {target_nick}")
        send(f"USER {target_nick} 0 * :{IRC_REALNAME}")
        registered = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = reader.readline()
            if not line:
                break
            line = line.rstrip("\r\n")
            if line.startswith("PING "):
                send("PONG " + line.split(" ", 1)[1])
                continue
            if " 433 " in line:
                raise IRCError("name_in_use")
            if " 001 " in line or " 422 " in line or " 376 " in line:
                registered = True
                break
        if not registered:
            raise IRCError("registration_failed")
        send(f"JOIN {target_channel}")
        send(f"PRIVMSG {target_channel} :{payload}")
        send("QUIT :bye")
    finally:
        try:
            writer.close()
            reader.close()
        finally:
            sock.close()
