from __future__ import annotations

import datetime as dt
import time


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) > 5 and (text[-5] in {"+", "-"}) and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    try:
        return dt.datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None
