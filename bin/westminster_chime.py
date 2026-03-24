#!/usr/bin/env python3

"""Play Westminster quarter chimes as a standalone alert utility."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import signal
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

DEFAULT_MIDI_PATH = Path("/home/david/random/bin/westminster-chimes.mid")
STATE_DIR = Path("/home/david/.local/state/westminster_chime")
SETTINGS_PATH = STATE_DIR / "settings.json"
DEFAULT_MUTED_MINUTES = 60
GUI_BG = "#0f141a"
GUI_PANEL = "#18212b"
GUI_TEXT = "#e8eef7"
GUI_MUTED = "#93a4b7"
GUI_ACCENT = "#5aa9e6"
GUI_ACCENT_ACTIVE = "#7cbbe9"
GUI_BUTTON = "#2b3440"
TRAY_ICON_NAMES = [
    "appointment-soon",
    "alarm",
    "preferences-system-time",
]

PLAYER_CANDIDATES = {
    "ffplay": ["ffplay", "-v", "quiet", "-nodisp", "-autoexit"],
    "paplay": ["paplay"],
    "aplay": ["aplay", "-q"],
}


def default_settings() -> dict[str, object]:
    return {
        "chime_enabled": True,
        "chime_active_hours": None,
        "chime_quarters": [0, 15, 30, 45],
        "hour_strike": True,
        "announcement_enabled": True,
        "announcement_hours": None,
        "mute_until": None,
    }


def load_settings() -> dict[str, object]:
    settings = default_settings()
    try:
        parsed = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return settings
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return settings

    if not isinstance(parsed, dict):
        return settings

    settings["chime_enabled"] = bool(
        parsed.get("chime_enabled", parsed.get("enabled", settings["chime_enabled"]))
    )
    active_hours = parsed.get("chime_active_hours", parsed.get("active_hours"))
    if active_hours in (None, ""):
        settings["chime_active_hours"] = None
    elif isinstance(active_hours, str):
        settings["chime_active_hours"] = active_hours
    chime_quarters = parsed.get("chime_quarters")
    if isinstance(chime_quarters, list):
        clean_quarters = sorted(
            {
                int(value)
                for value in chime_quarters
                if int(value) in (0, 15, 30, 45)
            }
        )
        if clean_quarters:
            settings["chime_quarters"] = clean_quarters
    settings["hour_strike"] = bool(parsed.get("hour_strike", settings["hour_strike"]))
    settings["announcement_enabled"] = bool(
        parsed.get("announcement_enabled", settings["announcement_enabled"])
    )
    announcement_hours = parsed.get("announcement_hours")
    if isinstance(announcement_hours, list):
        clean_hours = sorted({int(value) for value in announcement_hours if 0 <= int(value) <= 23})
        settings["announcement_hours"] = clean_hours or None

    mute_until = parsed.get("mute_until")
    if isinstance(mute_until, str) and mute_until.strip():
        settings["mute_until"] = mute_until
    return settings


def save_settings(settings: dict[str, object]) -> None:
    payload = {
        "chime_enabled": bool(settings.get("chime_enabled", True)),
        "chime_active_hours": settings.get("chime_active_hours") or None,
        "chime_quarters": sorted(
            {int(value) for value in (settings.get("chime_quarters") or []) if int(value) in (0, 15, 30, 45)}
        )
        or [0, 15, 30, 45],
        "hour_strike": bool(settings.get("hour_strike", True)),
        "announcement_enabled": bool(settings.get("announcement_enabled", True)),
        "announcement_hours": sorted(
            {int(value) for value in (settings.get("announcement_hours") or []) if 0 <= int(value) <= 23}
        )
        or None,
        "mute_until": settings.get("mute_until") or None,
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = SETTINGS_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(SETTINGS_PATH)


def parse_iso_timestamp(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def clear_expired_mute(settings: dict[str, object], now: dt.datetime) -> bool:
    mute_until = parse_iso_timestamp(settings.get("mute_until"))
    if mute_until is None:
        if settings.get("mute_until"):
            settings["mute_until"] = None
            return True
        return False
    if mute_until <= now:
        settings["mute_until"] = None
        return True
    return False


def effective_active_hours(args: argparse.Namespace, settings: dict[str, object]) -> str | None:
    return args.active_hours if args.active_hours is not None else settings.get("chime_active_hours")


def allowed_chime_quarters(settings: dict[str, object]) -> set[int]:
    values = settings.get("chime_quarters") or [0, 15, 30, 45]
    return {int(value) for value in values if int(value) in (0, 15, 30, 45)}


def chime_quarter_allowed(moment: dt.datetime, settings: dict[str, object]) -> bool:
    quarter = quarter_index(moment)
    if quarter is None:
        return False
    minute = 0 if quarter == 4 else quarter * 15
    return minute in allowed_chime_quarters(settings)


def allowed_announcement_hours(settings: dict[str, object]) -> set[int] | None:
    values = settings.get("announcement_hours")
    if not values:
        return None
    clean = {int(value) for value in values if 0 <= int(value) <= 23}
    return clean or None


def announcement_allowed(moment: dt.datetime, settings: dict[str, object]) -> bool:
    if not bool(settings.get("announcement_enabled", True)):
        return False
    hours = allowed_announcement_hours(settings)
    if hours is None:
        return True
    return moment.hour in hours


def should_play_chime(moment: dt.datetime, args: argparse.Namespace, settings: dict[str, object]) -> bool:
    if not chime_enabled(settings, moment):
        return False
    if not chime_quarter_allowed(moment, settings):
        return False
    active_hours = effective_active_hours(args, settings)
    return is_within_active_window(moment, active_hours)


def cron_active_hours() -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return (None, None)
    if result.returncode != 0:
        return (None, None)

    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "westminster_chime.py" not in line:
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        _minute_field, hour_field, _day, _month, _weekday, _command = parts
        derived = derive_active_hours_from_cron_hour_field(hour_field)
        if derived is not None:
            return (derived, line)
    return (None, None)


def cron_chime_schedule() -> tuple[str | None, list[int] | None, str | None]:
    try:
        result = subprocess.run(["crontab", "-l"], check=False, capture_output=True, text=True)
    except OSError:
        return (None, None, None)
    if result.returncode != 0:
        return (None, None, None)
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "westminster_chime.py" not in line:
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        minute_field, hour_field, _day, _month, _weekday, _command = parts
        return (
            derive_active_hours_from_cron_hour_field(hour_field),
            derive_quarters_from_cron_minute_field(minute_field),
            line,
        )
    return (None, None, None)


def cron_announcement_hours() -> tuple[list[int] | None, str | None]:
    try:
        result = subprocess.run(["crontab", "-l"], check=False, capture_output=True, text=True)
    except OSError:
        return (None, None)
    if result.returncode != 0:
        return (None, None)
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "presence_greeting_11speak.sh" not in line:
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        _minute_field, hour_field, _day, _month, _weekday, _command = parts
        return (derive_hours_from_cron_hour_field(hour_field), line)
    return (None, None)


def derive_active_hours_from_cron_hour_field(hour_field: str) -> str | None:
    field = hour_field.strip()
    if not field or field == "*":
        return "00:00-24:00"
    if "," in field:
        return None
    if "-" in field:
        start_text, end_text = field.split("-", 1)
        if not (start_text.isdigit() and end_text.isdigit()):
            return None
        start_hour = int(start_text)
        end_hour = int(end_text)
        if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
            return None
        end_exclusive = min(end_hour + 1, 24)
        return f"{start_hour:02d}:00-{end_exclusive:02d}:00"
    if field.isdigit():
        hour_value = int(field)
        if not (0 <= hour_value <= 23):
            return None
        end_exclusive = min(hour_value + 1, 24)
        return f"{hour_value:02d}:00-{end_exclusive:02d}:00"
    return None


def derive_hours_from_cron_hour_field(hour_field: str) -> list[int] | None:
    field = hour_field.strip()
    if not field:
        return None
    if field == "*":
        return list(range(24))
    values: set[int] = set()
    for part in field.split(","):
        token = part.strip()
        if not token:
            return None
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            if not (start_text.isdigit() and end_text.isdigit()):
                return None
            start_hour = int(start_text)
            end_hour = int(end_text)
            if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
                return None
            values.update(range(start_hour, end_hour + 1))
            continue
        if not token.isdigit():
            return None
        hour_value = int(token)
        if not (0 <= hour_value <= 23):
            return None
        values.add(hour_value)
    return sorted(values)


def derive_quarters_from_cron_minute_field(minute_field: str) -> list[int] | None:
    field = minute_field.strip()
    if not field:
        return None
    if field == "*":
        return [0, 15, 30, 45]
    if field == "*/15":
        return [0, 15, 30, 45]
    values: set[int] = set()
    for part in field.split(","):
        token = part.strip()
        if not token.isdigit():
            return None
        minute = int(token)
        if minute not in (0, 15, 30, 45):
            return None
        values.add(minute)
    return sorted(values)


def hour_strike_enabled(args: argparse.Namespace, settings: dict[str, object]) -> bool:
    if args.no_hour_strike:
        return False
    return bool(settings.get("hour_strike", True))


def chime_enabled(settings: dict[str, object], now: dt.datetime) -> bool:
    if not bool(settings.get("chime_enabled", True)):
        return False
    mute_until = parse_iso_timestamp(settings.get("mute_until"))
    if mute_until is None:
        return True
    return mute_until <= now


def midi_note_frequency(note_number: int) -> float:
    return 440.0 * (2 ** ((note_number - 69) / 12))


def note_name(note_number: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    octave = (note_number // 12) - 1
    return f"{names[note_number % 12]}{octave}"


def read_vlq(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            return value, offset


def load_midi_profile(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    if data[:4] != b"MThd":
        raise ValueError(f"{path} is not a MIDI file")

    header_length = int.from_bytes(data[4:8], "big")
    track_offset = 8 + header_length
    if data[track_offset : track_offset + 4] != b"MTrk":
        raise ValueError(f"{path} does not contain a track chunk")

    ticks_per_quarter = int.from_bytes(data[12:14], "big")
    track_length = int.from_bytes(data[track_offset + 4 : track_offset + 8], "big")
    offset = track_offset + 8
    end = offset + track_length
    tempo_us_per_quarter = 500000
    running_status: int | None = None
    current_tick = 0
    active_notes: dict[tuple[int, int], tuple[int, int]] = {}
    note_events: list[dict[str, float | int]] = []

    while offset < end:
        delta, offset = read_vlq(data, offset)
        current_tick += delta
        status = data[offset]
        if status < 0x80:
            if running_status is None:
                raise ValueError("MIDI running status used before initialization")
            status = running_status
        else:
            offset += 1
            running_status = status

        if status == 0xFF:
            meta_type = data[offset]
            offset += 1
            length, offset = read_vlq(data, offset)
            meta_data = data[offset : offset + length]
            offset += length
            if meta_type == 0x51:
                tempo_us_per_quarter = int.from_bytes(meta_data, "big")
            continue
        if status in (0xF0, 0xF7):
            length, offset = read_vlq(data, offset)
            offset += length
            continue

        kind = status & 0xF0
        channel = status & 0x0F
        if kind in (0x80, 0x90):
            note = data[offset]
            velocity = data[offset + 1]
            offset += 2
            key = (channel, note)
            if kind == 0x90 and velocity > 0:
                active_notes[key] = (current_tick, velocity)
            else:
                start_tick, start_velocity = active_notes.pop(key)
                note_events.append(
                    {
                        "note": note,
                        "velocity": start_velocity,
                        "start_tick": start_tick,
                        "duration_tick": current_tick - start_tick,
                    }
                )
            continue
        if kind in (0xA0, 0xB0, 0xE0):
            offset += 2
            continue
        if kind in (0xC0, 0xD0):
            offset += 1
            continue
        raise ValueError(f"Unsupported MIDI event: 0x{status:02x}")

    seconds_per_tick = (tempo_us_per_quarter / 1_000_000) / ticks_per_quarter
    melody = note_events[:16]
    strikes = [event for event in note_events[16:] if int(event["note"]) < 60]
    if len(melody) < 16:
        raise ValueError("MIDI file does not contain four Westminster phrases")
    if not strikes:
        strikes = [note_events[-1]]

    phrases: dict[int, list[dict[str, float | int]]] = {}
    for quarter in range(1, 5):
        phrase_slice = melody[: quarter * 4]
        phrases[quarter] = [
            {
                "note": int(event["note"]),
                "velocity": int(event["velocity"]),
                "duration_seconds": float(event["duration_tick"]) * seconds_per_tick,
                "frequency": midi_note_frequency(int(event["note"])),
                "name": note_name(int(event["note"])),
            }
            for event in phrase_slice
        ]

    strike_template = strikes[0]
    return {
        "source": str(path),
        "phrases": phrases,
        "strike_duration_seconds": float(strike_template["duration_tick"]) * seconds_per_tick,
        "strike_frequency": midi_note_frequency(int(strike_template["note"])),
        "strike_name": note_name(int(strike_template["note"])),
    }


def fallback_profile() -> dict[str, object]:
    fallback_phrases = {
        1: [64, 68, 66, 59],
        2: [64, 68, 66, 59, 64, 66, 68, 64],
        3: [64, 68, 66, 59, 64, 66, 68, 64, 68, 64, 66, 59],
        4: [64, 68, 66, 59, 64, 66, 68, 64, 68, 64, 66, 59, 59, 66, 68, 64],
    }
    return {
        "source": "fallback",
        "phrases": {
            quarter: [
                {
                    "note": midi_note,
                    "velocity": 64,
                    "duration_seconds": 0.75 if index % 4 != 3 else 2.25,
                    "frequency": midi_note_frequency(midi_note),
                    "name": note_name(midi_note),
                }
                for index, midi_note in enumerate(notes)
            ]
            for quarter, notes in fallback_phrases.items()
        },
        "strike_duration_seconds": 1.5,
        "strike_frequency": midi_note_frequency(52),
        "strike_name": note_name(52),
    }


def get_profile(midi_path: str) -> dict[str, object]:
    try:
        return load_midi_profile(Path(midi_path))
    except Exception:
        return fallback_profile()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Play Westminster quarter chimes for quarter-hour alerts."
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously and chime at each quarter hour.",
    )
    parser.add_argument(
        "--time",
        default="now",
        help="Time to simulate in HH:MM or 'now'. Ignored in --watch mode.",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "ffplay", "paplay", "aplay", "stdout"],
        default="auto",
        help="Audio playback backend or stdout for dry textual output.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the event instead of playing audio.",
    )
    parser.add_argument(
        "--no-hour-strike",
        action="store_true",
        help="Skip the hour count after the full-hour phrase.",
    )
    parser.add_argument(
        "--active-hours",
        metavar="START-END",
        help="Only chime inside a daily local-time window, e.g. 08:00-22:00.",
    )
    parser.add_argument(
        "--note-seconds",
        type=float,
        default=0.52,
        help="Duration of each melody note in seconds.",
    )
    parser.add_argument(
        "--strike-seconds",
        type=float,
        default=0.9,
        help="Duration of each hour strike in seconds.",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=0.45,
        help="Master output gain from 0.0 to 1.0.",
    )
    parser.add_argument(
        "--midi-path",
        default=str(DEFAULT_MIDI_PATH),
        help="MIDI file used as the source melody.",
    )
    parser.add_argument(
        "--dialog",
        choices=["auto", "on", "off"],
        default="auto",
        help="Show a transient control window while audio is playing.",
    )
    parser.add_argument(
        "--mute-minutes",
        type=int,
        default=DEFAULT_MUTED_MINUTES,
        help="Minutes to mute future chimes when the dialog mute button is used.",
    )
    parser.add_argument(
        "--configure",
        action="store_true",
        help="Open the settings dialog without playing audio.",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="Run an XFCE-compatible status tray icon for manual settings access.",
    )
    parser.add_argument(
        "--should-announce",
        action="store_true",
        help="Exit 0 when the current time is allowed to play the spoken announcement.",
    )
    parser.add_argument(
        "--should-chime",
        action="store_true",
        help="Exit 0 when the current time is allowed to play the chime.",
    )
    return parser.parse_args()


def resolve_backend(name: str) -> str:
    if name != "auto":
        return name
    for candidate in PLAYER_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    return "stdout"


def parse_clock_time(value: str, now: dt.datetime) -> dt.datetime:
    if value == "now":
        return now
    hour_text, minute_text = value.split(":", 1)
    return now.replace(
        hour=int(hour_text),
        minute=int(minute_text),
        second=0,
        microsecond=0,
    )


def parse_active_window(spec: str) -> tuple[dt.time, dt.time]:
    start_text, end_text = spec.split("-", 1)
    start = dt.time.fromisoformat(start_text)
    end = dt.time.fromisoformat(end_text)
    return start, end


def is_within_active_window(moment: dt.datetime, spec: str | None) -> bool:
    if not spec:
        return True
    start, end = parse_active_window(spec)
    current = moment.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def quarter_index(moment: dt.datetime) -> int | None:
    minute = moment.minute
    if minute not in (0, 15, 30, 45):
        return None
    if minute == 0:
        return 4
    return minute // 15


def strike_count(moment: dt.datetime) -> int:
    hour = moment.hour % 12
    return 12 if hour == 0 else hour


def describe_event(
    moment: dt.datetime,
    include_hour_strike: bool,
    profile: dict[str, object],
) -> str:
    quarter = quarter_index(moment)
    if quarter is None:
        return f"{moment:%H:%M} is not a quarter-hour boundary"

    phrase = profile["phrases"][quarter]
    note_text = " ".join(event["name"] for event in phrase)
    parts = [
        f"{moment:%H:%M}",
        f"quarter={quarter}",
        f"notes={note_text}",
        f"source={profile['source']}",
    ]
    if quarter == 4 and include_hour_strike:
        parts.append(f"strikes={strike_count(moment)}x{profile['strike_name']}")
    return " | ".join(parts)


def sine_bell(
    frequency: float,
    duration: float,
    sample_rate: int,
    volume: float,
) -> list[float]:
    frames: list[float] = []
    attack = min(0.03, duration / 6)
    decay = duration
    for index in range(int(duration * sample_rate)):
        t = index / sample_rate
        if t < attack:
            envelope = t / attack
        else:
            envelope = math.exp(-3.4 * (t - attack) / max(decay - attack, 0.001))
        sample = (
            math.sin(2 * math.pi * frequency * t)
            + 0.55 * math.sin(2 * math.pi * (frequency * 2) * t)
            + 0.18 * math.sin(2 * math.pi * (frequency * 3) * t)
        )
        frames.append(sample * envelope * volume / 1.73)
    return frames


def silence(duration: float, sample_rate: int) -> list[float]:
    return [0.0] * int(duration * sample_rate)


def build_audio(
    moment: dt.datetime,
    strike_seconds: float,
    volume: float,
    include_hour_strike: bool,
    profile: dict[str, object],
    sample_rate: int = 44_100,
) -> bytes:
    quarter = quarter_index(moment)
    if quarter is None:
        raise ValueError("time is not on a quarter-hour boundary")

    samples: list[float] = []
    note_gap = 0.055
    strike_gap = 0.42

    for event in profile["phrases"][quarter]:
        samples.extend(
            sine_bell(
                float(event["frequency"]),
                duration=float(event["duration_seconds"]),
                sample_rate=sample_rate,
                volume=volume,
            )
        )
        samples.extend(silence(note_gap, sample_rate))

    if quarter == 4 and include_hour_strike:
        samples.extend(silence(0.65, sample_rate))
        count = strike_count(moment)
        for _ in range(count):
            samples.extend(
                sine_bell(
                    float(profile["strike_frequency"]),
                    duration=max(strike_seconds, float(profile["strike_duration_seconds"])),
                    sample_rate=sample_rate,
                    volume=volume * 0.95,
                )
            )
            samples.extend(silence(strike_gap, sample_rate))

    pcm = bytearray()
    for sample in samples:
        clamped = max(-1.0, min(1.0, sample))
        pcm.extend(struct.pack("<h", int(clamped * 32767)))
    return bytes(pcm)


def write_wav(path: Path, pcm_data: bytes, sample_rate: int = 44_100) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)


def play_wav(path: Path, backend: str) -> None:
    if backend == "stdout":
        return
    command = PLAYER_CANDIDATES[backend] + [str(path)]
    subprocess.run(command, check=True)


def launch_player(path: Path, backend: str) -> subprocess.Popen[bytes]:
    command = PLAYER_CANDIDATES[backend] + [str(path)]
    return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_player(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1.2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.2)


def can_show_dialog(mode: str) -> bool:
    if mode == "off":
        return False
    if mode == "on":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def mute_until_text(settings: dict[str, object]) -> str:
    mute_until = parse_iso_timestamp(settings.get("mute_until"))
    if mute_until is None:
        return "No active mute."
    return f"Muted until {mute_until:%Y-%m-%d %I:%M %p}"


def apply_temporary_mute(settings: dict[str, object], minutes: int) -> None:
    mute_until = dt.datetime.now().astimezone() + dt.timedelta(minutes=max(int(minutes), 1))
    settings["mute_until"] = mute_until.isoformat()
    save_settings(settings)


def open_control_dialog(
    moment: dt.datetime | None,
    description: str,
    process: subprocess.Popen[bytes] | None,
    args: argparse.Namespace,
    settings: dict[str, object],
    temp_path: Path | None,
) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception as exc:  # pragma: no cover - environment specific
        raise RuntimeError(f"Tk unavailable: {exc}") from exc

    root = tk.Tk()
    root.title("Westminster Clock")
    root.configure(bg=GUI_BG)
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frame = tk.Frame(root, bg=GUI_BG, padx=16, pady=16)
    frame.pack(fill="both", expand=True)

    title = tk.Label(
        frame,
        text=f"Chiming {moment:%I:%M %p}" if moment else "Westminster Clock Settings",
        bg=GUI_BG,
        fg=GUI_TEXT,
        font=("Helvetica", 14, "bold"),
    )
    title.pack(anchor="w")

    summary = tk.Label(
        frame,
        text=description,
        bg=GUI_BG,
        fg=GUI_MUTED,
        justify="left",
        wraplength=560,
        font=("Helvetica", 10),
    )
    summary.pack(anchor="w", pady=(6, 12))

    button_row = tk.Frame(frame, bg=GUI_BG)
    button_row.pack(fill="x", pady=(0, 12))

    playback_active = process is not None
    state: dict[str, bool] = {"closed": False}

    def close_window(*, stop_audio: bool) -> None:
        if state["closed"]:
            return
        state["closed"] = True
        if stop_audio and process is not None:
            stop_player(process)
        root.after(0, root.destroy)

    def dismiss() -> None:
        close_window(stop_audio=playback_active)

    def mute_for_a_while() -> None:
        apply_temporary_mute(settings, args.mute_minutes)
        status_var.set(mute_until_text(settings))
        close_window(stop_audio=playback_active)

    if playback_active:
        tk.Button(
            button_row,
            text=f"Mute {max(int(args.mute_minutes), 1)}m",
            command=mute_for_a_while,
            bg=GUI_ACCENT,
            fg="#081018",
            activebackground=GUI_ACCENT_ACTIVE,
            activeforeground="#081018",
            relief="flat",
            padx=10,
            pady=6,
        ).pack(side="left")
    tk.Button(
        button_row,
        text="Dismiss" if playback_active else "Close",
        command=dismiss,
        bg=GUI_BUTTON,
        fg=GUI_TEXT,
        activebackground="#394553",
        activeforeground=GUI_TEXT,
        relief="flat",
        padx=10,
        pady=6,
    ).pack(side="left", padx=(8 if playback_active else 0, 0))

    status_var = tk.StringVar(value=mute_until_text(settings))
    schedule = tk.Frame(frame, bg=GUI_PANEL, padx=12, pady=12, highlightthickness=1, highlightbackground="#24313f")
    schedule.pack(fill="x")
    tk.Label(schedule, text="Chime Schedule", bg=GUI_PANEL, fg=GUI_TEXT, font=("Helvetica", 11, "bold")).grid(
        row=0, column=0, columnspan=6, sticky="w"
    )

    chime_enabled_var = tk.BooleanVar(value=bool(settings.get("chime_enabled", True)))
    hour_strike_var = tk.BooleanVar(value=bool(settings.get("hour_strike", True)))
    active_hours_source = "saved settings"
    active_hours_value = str(settings.get("chime_active_hours") or "")
    if not active_hours_value and args.active_hours:
        active_hours_value = args.active_hours
        active_hours_source = "command line"
    cron_hours_value, cron_quarters_value, _cron_line = cron_chime_schedule()
    if not active_hours_value and cron_hours_value:
        active_hours_value = cron_hours_value
        active_hours_source = "cron schedule"
    start_value, end_value = "", ""
    if active_hours_value:
        try:
            start_value, end_value = active_hours_value.split("-", 1)
        except ValueError:
            start_value, end_value = active_hours_value, ""
    start_var = tk.StringVar(value=start_value)
    end_var = tk.StringVar(value=end_value)
    current_active_hours_var = tk.StringVar()

    chime_quarters_source = "saved settings"
    stored_quarters = settings.get("chime_quarters")
    current_quarters = sorted(allowed_chime_quarters(settings))
    if stored_quarters in (None, [], [0, 15, 30, 45]) and cron_quarters_value:
        current_quarters = cron_quarters_value
        chime_quarters_source = "cron schedule"
    quarter_vars = {minute: tk.BooleanVar(value=minute in current_quarters) for minute in (0, 15, 30, 45)}
    current_quarters_var = tk.StringVar()

    def quarter_text(values: list[int]) -> str:
        labels = {0: ":00", 15: ":15", 30: ":30", 45: ":45"}
        picked = [labels[value] for value in sorted(values) if value in labels]
        return ", ".join(picked) if picked else "none"

    def refresh_chime_summary(hours_source_override: str | None = None, quarters_source_override: str | None = None) -> None:
        current_hours_value = (
            f"{start_var.get().strip()}-{end_var.get().strip()}"
            if start_var.get().strip() and end_var.get().strip()
            else "all hours"
        )
        current_active_hours_var.set(
            f"Current chime hours: {current_hours_value} ({hours_source_override or active_hours_source})"
        )
        current_quarters_var.set(
            f"Current chime marks: {quarter_text([value for value, var in quarter_vars.items() if var.get()])} ({quarters_source_override or chime_quarters_source})"
        )

    tk.Checkbutton(
        schedule,
        text="Enable chimes",
        variable=chime_enabled_var,
        bg=GUI_PANEL,
        fg=GUI_TEXT,
        activebackground=GUI_PANEL,
        activeforeground=GUI_TEXT,
        selectcolor=GUI_PANEL,
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 6))
    tk.Checkbutton(
        schedule,
        text="Hour strike at :00",
        variable=hour_strike_var,
        bg=GUI_PANEL,
        fg=GUI_TEXT,
        activebackground=GUI_PANEL,
        activeforeground=GUI_TEXT,
        selectcolor=GUI_PANEL,
    ).grid(row=1, column=2, columnspan=2, sticky="w", pady=(10, 6))

    tk.Label(schedule, text="Start", bg=GUI_PANEL, fg=GUI_MUTED).grid(row=2, column=0, sticky="w", pady=(4, 2))
    tk.Label(schedule, text="End", bg=GUI_PANEL, fg=GUI_MUTED).grid(row=2, column=1, sticky="w", pady=(4, 2))
    start_entry = tk.Entry(schedule, textvariable=start_var, width=10, bg="#101820", fg=GUI_TEXT, insertbackground=GUI_TEXT, relief="flat")
    end_entry = tk.Entry(schedule, textvariable=end_var, width=10, bg="#101820", fg=GUI_TEXT, insertbackground=GUI_TEXT, relief="flat")
    start_entry.grid(row=3, column=0, sticky="w")
    end_entry.grid(row=3, column=1, sticky="w")
    tk.Label(schedule, text="Use HH:MM. Leave both blank for all hours.", bg=GUI_PANEL, fg=GUI_MUTED).grid(
        row=3, column=2, columnspan=4, sticky="w"
    )
    tk.Label(schedule, text="Quarter Marks", bg=GUI_PANEL, fg=GUI_MUTED).grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 2))
    for column_index, minute in enumerate((0, 15, 30, 45)):
        tk.Checkbutton(
            schedule,
            text=f":{minute:02d}",
            variable=quarter_vars[minute],
            bg=GUI_PANEL,
            fg=GUI_TEXT,
            activebackground=GUI_PANEL,
            activeforeground=GUI_TEXT,
            selectcolor=GUI_PANEL,
        ).grid(row=5, column=column_index, sticky="w")
    tk.Label(schedule, textvariable=current_active_hours_var, bg=GUI_PANEL, fg=GUI_MUTED, wraplength=520, justify="left").grid(
        row=6, column=0, columnspan=6, sticky="w", pady=(10, 0)
    )
    tk.Label(schedule, textvariable=current_quarters_var, bg=GUI_PANEL, fg=GUI_MUTED, wraplength=520, justify="left").grid(
        row=7, column=0, columnspan=6, sticky="w"
    )

    announcement = tk.Frame(frame, bg=GUI_PANEL, padx=12, pady=12, highlightthickness=1, highlightbackground="#24313f")
    announcement.pack(fill="x", pady=(12, 0))
    tk.Label(
        announcement,
        text="Announcement Schedule",
        bg=GUI_PANEL,
        fg=GUI_TEXT,
        font=("Helvetica", 11, "bold"),
    ).grid(row=0, column=0, columnspan=6, sticky="w")
    announcement_enabled_var = tk.BooleanVar(value=bool(settings.get("announcement_enabled", True)))
    announcement_source = "saved settings"
    announcement_hours = settings.get("announcement_hours")
    cron_announcement_value, _announcement_line = cron_announcement_hours()
    if not announcement_hours and cron_announcement_value:
        announcement_hours = cron_announcement_value
        announcement_source = "cron schedule"
    selected_announcement_hours = sorted(
        {int(value) for value in (announcement_hours or []) if 0 <= int(value) <= 23}
    )
    announcement_vars = {
        hour_value: tk.BooleanVar(value=hour_value in selected_announcement_hours)
        for hour_value in range(24)
    }
    current_announcement_var = tk.StringVar()

    def announcement_hours_text(values: list[int]) -> str:
        if not values:
            return "all hours"
        return ", ".join(f"{value:02d}:00" for value in values)

    def refresh_announcement_summary(source_override: str | None = None) -> None:
        current_announcement_var.set(
            f"Current announcement hours: {announcement_hours_text([hour_value for hour_value, var in announcement_vars.items() if var.get()])} ({source_override or announcement_source})"
        )

    tk.Checkbutton(
        announcement,
        text="Enable spoken announcement",
        variable=announcement_enabled_var,
        bg=GUI_PANEL,
        fg=GUI_TEXT,
        activebackground=GUI_PANEL,
        activeforeground=GUI_TEXT,
        selectcolor=GUI_PANEL,
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 6))
    tk.Label(announcement, text="Allowed Hours", bg=GUI_PANEL, fg=GUI_MUTED).grid(row=2, column=0, columnspan=6, sticky="w")
    for hour_value in range(24):
        row_index = 3 + (hour_value // 6)
        column_index = hour_value % 6
        tk.Checkbutton(
            announcement,
            text=f"{hour_value:02d}:00",
            variable=announcement_vars[hour_value],
            bg=GUI_PANEL,
            fg=GUI_TEXT,
            activebackground=GUI_PANEL,
            activeforeground=GUI_TEXT,
            selectcolor=GUI_PANEL,
        ).grid(row=row_index, column=column_index, sticky="w")
    tk.Label(
        announcement,
        text="If none are checked, announcements are allowed at any hour.",
        bg=GUI_PANEL,
        fg=GUI_MUTED,
        wraplength=520,
        justify="left",
    ).grid(row=7, column=0, columnspan=6, sticky="w", pady=(8, 0))
    tk.Label(
        announcement,
        textvariable=current_announcement_var,
        bg=GUI_PANEL,
        fg=GUI_MUTED,
        wraplength=520,
        justify="left",
    ).grid(row=8, column=0, columnspan=6, sticky="w", pady=(8, 0))

    def save_schedule() -> None:
        nonlocal active_hours_source, chime_quarters_source, announcement_source
        start_text = start_var.get().strip()
        end_text = end_var.get().strip()
        chime_active_hours = None
        if start_text or end_text:
            if not (start_text and end_text):
                messagebox.showerror("Schedule", "Provide both chime start and end times, or leave both blank.")
                return
            chime_active_hours = f"{start_text}-{end_text}"
            try:
                parse_active_window(chime_active_hours)
            except ValueError:
                messagebox.showerror("Schedule", "Use HH:MM-HH:MM, for example 08:00-22:00.")
                return
        selected_quarters = sorted([value for value, var in quarter_vars.items() if var.get()])
        if not selected_quarters:
            messagebox.showerror("Schedule", "Select at least one chime quarter mark.")
            return
        selected_announcement = sorted([hour_value for hour_value, var in announcement_vars.items() if var.get()])
        settings["chime_enabled"] = chime_enabled_var.get()
        settings["chime_active_hours"] = chime_active_hours
        settings["chime_quarters"] = selected_quarters
        settings["hour_strike"] = hour_strike_var.get()
        settings["announcement_enabled"] = announcement_enabled_var.get()
        settings["announcement_hours"] = selected_announcement or None
        save_settings(settings)
        active_hours_source = "saved settings"
        chime_quarters_source = "saved settings"
        announcement_source = "saved settings"
        refresh_chime_summary("saved settings", "saved settings")
        refresh_announcement_summary("saved settings")
        status_var.set(f"Saved at {dt.datetime.now().astimezone():%H:%M:%S}. {mute_until_text(settings)}")

    refresh_chime_summary()
    refresh_announcement_summary()

    footer = tk.Frame(frame, bg=GUI_BG)
    footer.pack(fill="x", pady=(12, 0))
    tk.Button(
        footer,
        text="Save Schedule",
        command=save_schedule,
        bg=GUI_BUTTON,
        fg=GUI_TEXT,
        activebackground="#394553",
        activeforeground=GUI_TEXT,
        relief="flat",
        padx=10,
        pady=6,
    ).pack(side="left")
    tk.Label(footer, textvariable=status_var, bg=GUI_BG, fg=GUI_MUTED, wraplength=420, justify="left").pack(
        side="left", padx=(12, 0)
    )

    def poll_player() -> None:
        if process is None:
            return
        if state["closed"]:
            return
        if process.poll() is not None:
            close_window(stop_audio=False)
            return
        root.after(150, poll_player)

    def cleanup() -> None:
        if process is not None:
            stop_player(process)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    root.protocol("WM_DELETE_WINDOW", dismiss)
    root.bind("<Escape>", lambda _event: dismiss())
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    screen_width = root.winfo_screenwidth()
    x_coord = max(screen_width - width - 32, 24)
    y_coord = 36
    root.geometry(f"+{x_coord}+{y_coord}")
    if playback_active:
        poll_player()
    try:
        root.mainloop()
    finally:
        cleanup()


def play_event(moment: dt.datetime, args: argparse.Namespace, settings: dict[str, object]) -> None:
    include_hour_strike = hour_strike_enabled(args, settings)
    profile = get_profile(args.midi_path)
    description = describe_event(moment, include_hour_strike, profile)
    if args.dry_run or args.backend == "stdout":
        print(description)
        return

    pcm_data = build_audio(
        moment,
        strike_seconds=args.strike_seconds,
        volume=max(0.0, min(1.0, args.volume)),
        include_hour_strike=include_hour_strike,
        profile=profile,
    )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        write_wav(temp_path, pcm_data)
        backend = resolve_backend(args.backend)
        if can_show_dialog(args.dialog):
            process = launch_player(temp_path, backend)
            try:
                open_control_dialog(moment, description, process, args, settings, temp_path)
                return
            except Exception as exc:  # pragma: no cover - display/toolkit specific
                stop_player(process)
                print(f"Dialog unavailable, falling back to headless playback: {exc}", file=sys.stderr)
        play_wav(temp_path, backend)
    finally:
        temp_path.unlink(missing_ok=True)


def open_settings_dialog(args: argparse.Namespace, settings: dict[str, object]) -> None:
    open_control_dialog(
        None,
        "Adjust chime schedule, mute state, and hour strike behavior.",
        None,
        args,
        settings,
        None,
    )


def spawn_configure_process(args: argparse.Namespace) -> None:
    subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--configure",
            "--dialog",
            "on",
            "--mute-minutes",
            str(max(int(args.mute_minutes), 1)),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def choose_tray_indicator():
    import gi

    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3

        return AyatanaAppIndicator3
    except (ImportError, ValueError):
        pass
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3

        return AppIndicator3
    except (ImportError, ValueError):
        pass
    raise RuntimeError("Neither AyatanaAppIndicator3 nor AppIndicator3 is available.")


def run_tray_mode(args: argparse.Namespace) -> int:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except Exception as exc:
        print(f"Tray mode unavailable: Gtk is missing: {exc}", file=sys.stderr)
        return 1

    try:
        indicator_module = choose_tray_indicator()
    except Exception as exc:
        print(f"Tray mode unavailable: {exc}", file=sys.stderr)
        return 1

    Indicator = indicator_module.Indicator
    IndicatorCategory = indicator_module.IndicatorCategory
    IndicatorStatus = indicator_module.IndicatorStatus

    indicator = Indicator.new(
        "westminster-clock",
        TRAY_ICON_NAMES[0],
        IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_status(IndicatorStatus.ACTIVE)
    indicator.set_title("Westminster Clock")
    for icon_name in TRAY_ICON_NAMES:
        try:
            indicator.set_icon_full(icon_name, "Westminster Clock")
            break
        except TypeError:
            continue

    menu = Gtk.Menu()

    def refresh_label() -> None:
        settings = load_settings()
        if clear_expired_mute(settings, dt.datetime.now().astimezone()):
            save_settings(settings)
        enabled_text = "on" if bool(settings.get("chime_enabled", True)) else "off"
        active_hours = settings.get("chime_active_hours") or "all hours"
        mute_text = mute_until_text(settings)
        summary_item.set_label(f"Westminster: {enabled_text} | {active_hours} | {mute_text}")

    summary_item = Gtk.MenuItem(label="Westminster Clock")
    summary_item.set_sensitive(False)
    menu.append(summary_item)

    open_item = Gtk.MenuItem(label="Open Settings")
    open_item.connect("activate", lambda *_args: spawn_configure_process(args))
    menu.append(open_item)

    mute_item = Gtk.MenuItem(label=f"Mute {max(int(args.mute_minutes), 1)} Minutes")

    def on_mute(*_args) -> None:
        settings = load_settings()
        apply_temporary_mute(settings, args.mute_minutes)
        refresh_label()

    mute_item.connect("activate", on_mute)
    menu.append(mute_item)

    unmute_item = Gtk.MenuItem(label="Clear Mute")

    def on_unmute(*_args) -> None:
        settings = load_settings()
        settings["mute_until"] = None
        save_settings(settings)
        refresh_label()

    unmute_item.connect("activate", on_unmute)
    menu.append(unmute_item)

    toggle_item = Gtk.MenuItem(label="Toggle Chimes")

    def on_toggle(*_args) -> None:
        settings = load_settings()
        settings["chime_enabled"] = not bool(settings.get("chime_enabled", True))
        save_settings(settings)
        refresh_label()

    toggle_item.connect("activate", on_toggle)
    menu.append(toggle_item)

    quit_item = Gtk.MenuItem(label="Quit Tray")
    quit_item.connect("activate", lambda *_args: Gtk.main_quit())
    menu.append(quit_item)

    menu.show_all()
    indicator.set_menu(menu)
    refresh_label()

    def on_signal(_signum, _frame) -> None:
        Gtk.main_quit()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    Gtk.main()
    return 0


def sleep_until_next_quarter(now: dt.datetime) -> None:
    next_minute = ((now.minute // 15) + 1) * 15
    next_hour = now.hour
    next_day = now.date()
    if next_minute >= 60:
        next_minute = 0
        next_hour += 1
        if next_hour >= 24:
            next_hour = 0
            next_day = now.date() + dt.timedelta(days=1)
    target = dt.datetime.combine(
        next_day,
        dt.time(hour=next_hour, minute=next_minute),
        tzinfo=now.tzinfo,
    )
    delay = max(0.0, (target - now).total_seconds())
    time.sleep(delay)


def run_watch_mode(args: argparse.Namespace) -> int:
    while True:
        now = dt.datetime.now().astimezone()
        settings = load_settings()
        if clear_expired_mute(settings, now):
            save_settings(settings)
        sleep_until_next_quarter(now)
        moment = dt.datetime.now().astimezone().replace(second=0, microsecond=0)
        settings = load_settings()
        if clear_expired_mute(settings, moment):
            save_settings(settings)
        if not chime_enabled(settings, moment):
            print(f"Skipping {moment:%H:%M}; chime muted or disabled", file=sys.stderr)
            continue
        if not chime_quarter_allowed(moment, settings):
            print(f"Skipping {moment:%H:%M}; quarter mark disabled by settings", file=sys.stderr)
            continue
        active_hours = effective_active_hours(args, settings)
        if not is_within_active_window(moment, active_hours):
            print(f"Skipping {moment:%H:%M}; outside active window", file=sys.stderr)
            continue
        profile = get_profile(args.midi_path)
        include_hour_strike = hour_strike_enabled(args, settings)
        print(describe_event(moment, include_hour_strike, profile))
        try:
            play_event(moment, args, settings)
        except Exception as exc:  # pragma: no cover - operational guardrail
            print(f"Playback failed at {moment:%H:%M}: {exc}", file=sys.stderr)
    return 0


def main() -> int:
    args = parse_args()
    args.backend = resolve_backend(args.backend)
    settings = load_settings()
    now = dt.datetime.now().astimezone()
    if clear_expired_mute(settings, now):
        save_settings(settings)

    if args.tray:
        return run_tray_mode(args)

    if args.configure:
        open_settings_dialog(args, settings)
        return 0

    if args.watch:
        return run_watch_mode(args)

    moment = parse_clock_time(args.time, now)
    if args.should_announce:
        return 0 if announcement_allowed(moment, settings) else 1
    if args.should_chime:
        return 0 if should_play_chime(moment, args, settings) else 1
    if not chime_enabled(settings, moment):
        print(f"Muted or disabled by settings: {moment:%H:%M}", file=sys.stderr)
        return 0
    if not chime_quarter_allowed(moment, settings):
        print(f"Muted by quarter schedule: {moment:%H:%M}", file=sys.stderr)
        return 0
    active_hours = effective_active_hours(args, settings)
    if not is_within_active_window(moment, active_hours):
        print(f"Muted by active window: {moment:%H:%M}", file=sys.stderr)
        return 0

    quarter = quarter_index(moment)
    if quarter is None:
        print(
            "Choose a quarter-hour boundary such as 09:15, 09:30, 09:45, or 10:00.",
            file=sys.stderr,
        )
        return 2

    play_event(moment, args, settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
