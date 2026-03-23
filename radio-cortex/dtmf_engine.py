from __future__ import annotations

import re
from collections import deque
from typing import Any

from utils.time import now_iso


DTMF_REGEX = re.compile(
    r"(?:(?P<prefix>[A-Z0-9_./-]+):\s*)?"
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}):\s*"
    r"DTMF:\s*(?P<tone>[0-9A-D#*])\b"
)


def parse_dtmf_line(line: str, *, default_channel: str = "unknown") -> dict[str, Any] | None:
    match = DTMF_REGEX.search(line.strip())
    if not match:
        return None
    channel = (match.group("prefix") or "").strip(" :") or default_channel
    timestamp = match.group("ts").strip()
    tone = match.group("tone").strip()
    return {
        "type": "dtmf",
        "tone": tone,
        "timestamp": timestamp,
        "ts": now_iso(),
        "channel": channel,
        "raw_line": line.strip(),
        "position": "unknown",
    }


class DTMFSequenceTracker:
    def __init__(self, max_tones: int = 5, max_gap_seconds: float = 15.0) -> None:
        self.max_tones = max_tones
        self.max_gap_seconds = max_gap_seconds
        self._events: deque[dict[str, Any]] = deque(maxlen=max_tones)

    def update(self, event: dict[str, Any], event_ts: float | None) -> dict[str, Any]:
        if self._events and event_ts is not None:
            last_ts = self._events[-1].get("_parsed_ts")
            if last_ts is not None and (event_ts - last_ts) > self.max_gap_seconds:
                self._events.clear()
        entry = dict(event)
        entry["_parsed_ts"] = event_ts
        self._events.append(entry)
        sequence = "".join(str(item.get("tone") or "") for item in self._events)
        return {
            "sequence": sequence,
            "tones": [str(item.get("tone") or "") for item in self._events],
            "length": len(self._events),
        }


def classify_dtmf(tone: str, sequence: str) -> str:
    if tone in {"A", "B", "C", "D"}:
        return "control"
    if len(sequence) >= 2:
        return "sequence"
    return "unknown"
