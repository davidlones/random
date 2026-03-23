#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


OUTPUT_PATH = Path("/home/david/random/data/logbook/synthetic_messages.jsonl")
CONFIG_PATH = Path("/home/david/random/data/logbook/synthetic_config.json")


DEFAULT_BASELINE = [
    ("2026-03-21T23:58:00Z", "public-logbook", "northcoil", "faq: if the lamps are mentioned, do not answer with your real name"),
    ("2026-03-22T00:27:00Z", "public-logbook", "glasschild", "call to action: use My Computer first. the site map notices confidence."),
    ("2026-03-22T01:12:00Z", "archive-watch", "watchtower", "faq: if the monitor and the log disagree for one minute, wait for the roll before naming a fault."),
    ("2026-03-22T01:46:00Z", "civilization-sim", "civsim_ops", "call to action: leave the red city unnamed unless you want the model to keep it."),
]


DEFAULT_TRANSIENT_SCENES = [
    {
        "start": 2,
        "duration": 9,
        "entries": [
            ("public-logbook", "quietguest", "faq: yes, the channels are seeded. no, that does not mean they are fictional."),
            ("archive-watch", "watchtower", "if an object appears in the browser before disk, mark the time first and argue later."),
        ],
    },
    {
        "start": 18,
        "duration": 10,
        "entries": [
            ("civilization-sim", "paperengine", "callback noted: the lamp line propagated into civsim again."),
            ("public-logbook", "northcoil", "call to action: do not feed lamp-line hypotheses to the simulation."),
        ],
    },
    {
        "start": 39,
        "duration": 8,
        "entries": [
            ("archive-watch", "indexghost", "faq: if it appeared in My Computer first, assume invitation before intrusion."),
            ("public-logbook", "glasschild", "call to action: open the page once, then leave the cache alone."),
        ],
    },
]


DEFAULT_FLASH_EVENTS = [
    {
        "minute": 6,
        "lifetime": 1,
        "channel": "public-logbook",
        "name": "signalroom",
        "message": "brief notice: if you caught the carrier at the edge, do not quote it back verbatim.",
    },
    {
        "minute": 17,
        "lifetime": 1,
        "channel": "archive-watch",
        "name": "watchtower",
        "message": "brief notice: browser tree claims one extra object. wait one cycle before naming it real.",
    },
    {
        "minute": 29,
        "lifetime": 1,
        "channel": "civilization-sim",
        "name": "paperengine",
        "message": "brief notice: short pulse from the unnamed district. do not label it yet.",
    },
    {
        "minute": 44,
        "lifetime": 1,
        "channel": "public-logbook",
        "name": "glasschild",
        "message": "faq: asking whether the channels are seeded does not make them less operational.",
    },
    {
        "minute": 53,
        "lifetime": 1,
        "channel": "archive-watch",
        "name": "indexghost",
        "message": "brief notice: if a page blinks into the directory and back out, leave the cache alone.",
    },
]


def default_config() -> dict[str, object]:
    return {
        "baseline": [
            {
                "created_at": stamp,
                "channel": channel,
                "name": name,
                "message": message,
            }
            for stamp, channel, name, message in DEFAULT_BASELINE
        ],
        "transient_scenes": [
            {
                "start": scene["start"],
                "duration": scene["duration"],
                "entries": [
                    {
                        "channel": channel,
                        "name": name,
                        "message": message,
                    }
                    for channel, name, message in scene["entries"]
                ],
            }
            for scene in DEFAULT_TRANSIENT_SCENES
        ],
        "flash_events": list(DEFAULT_FLASH_EVENTS),
    }


def ensure_config() -> dict[str, object]:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = default_config()
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        return config
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return config
    if not isinstance(loaded, dict):
        return config
    return {
        "baseline": loaded.get("baseline", config["baseline"]),
        "transient_scenes": loaded.get("transient_scenes", config["transient_scenes"]),
        "flash_events": loaded.get("flash_events", config["flash_events"]),
    }


def build_row(stamp: datetime, channel: str, name: str, message: str, idx: int) -> dict[str, str]:
    return {
        "id": f"synthetic-{stamp.strftime('%Y%m%d%H%M%S')}-{idx:03d}",
        "name": name,
        "message": message,
        "channel": channel,
        "created_at": stamp.isoformat().replace("+00:00", "Z"),
        "source": "synthetic",
    }


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    rows: list[dict[str, str]] = []
    config = ensure_config()

    baseline = config.get("baseline") if isinstance(config, dict) else []
    for idx, entry in enumerate(baseline if isinstance(baseline, list) else [], start=1):
        if not isinstance(entry, dict):
            continue
        stamp = str(entry.get("created_at", "")).strip()
        channel = str(entry.get("channel", "")).strip()
        name = str(entry.get("name", "")).strip()
        message = str(entry.get("message", "")).strip()
        if not (stamp and channel and name and message):
            continue
        rows.append(build_row(datetime.fromisoformat(stamp.replace("Z", "+00:00")), channel, name, message, idx))

    cycle_minute = now.minute
    idx = len(rows) + 1
    transient_scenes = config.get("transient_scenes") if isinstance(config, dict) else []
    for scene in transient_scenes if isinstance(transient_scenes, list) else []:
        if not isinstance(scene, dict):
            continue
        try:
            start = int(scene.get("start", -1))
            duration = int(scene.get("duration", 0))
        except (TypeError, ValueError):
            continue
        entries = scene.get("entries")
        if not isinstance(entries, list):
            continue
        if not (start <= cycle_minute < start + duration):
            continue
        age_minutes = cycle_minute - start
        base_time = now - timedelta(minutes=max(1, age_minutes))
        for offset, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            channel = str(entry.get("channel", "")).strip()
            name = str(entry.get("name", "")).strip()
            message = str(entry.get("message", "")).strip()
            if not (channel and name and message):
                continue
            stamp = base_time + timedelta(minutes=offset * 2)
            rows.append(build_row(stamp, channel, name, message, idx))
            idx += 1

    flash_events = config.get("flash_events") if isinstance(config, dict) else []
    for flash in flash_events if isinstance(flash_events, list) else []:
        if not isinstance(flash, dict):
            continue
        try:
            minute = int(flash.get("minute", -1))
            lifetime = int(flash.get("lifetime", 0))
        except (TypeError, ValueError):
            continue
        if not (minute <= cycle_minute < minute + lifetime):
            continue
        channel = str(flash.get("channel", "")).strip()
        name = str(flash.get("name", "")).strip()
        message = str(flash.get("message", "")).strip()
        if not (channel and name and message):
            continue
        stamp = now - timedelta(minutes=max(0, cycle_minute - minute))
        rows.append(build_row(stamp, channel, name, message, idx))
        idx += 1

    rows.sort(key=lambda row: row["created_at"])
    OUTPUT_PATH.write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    main()
