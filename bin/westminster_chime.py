#!/usr/bin/env python3

"""Play Westminster quarter chimes as a standalone alert utility."""

from __future__ import annotations

import argparse
import datetime as dt
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

DEFAULT_MIDI_PATH = Path("/home/david/random/bin/westminster-chimes.mid")

PLAYER_CANDIDATES = {
    "ffplay": ["ffplay", "-v", "quiet", "-nodisp", "-autoexit"],
    "paplay": ["paplay"],
    "aplay": ["aplay", "-q"],
}


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


def play_event(moment: dt.datetime, args: argparse.Namespace) -> None:
    include_hour_strike = not args.no_hour_strike
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
        play_wav(temp_path, resolve_backend(args.backend))
    finally:
        temp_path.unlink(missing_ok=True)


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
    include_hour_strike = not args.no_hour_strike
    profile = get_profile(args.midi_path)
    while True:
        now = dt.datetime.now().astimezone()
        sleep_until_next_quarter(now)
        moment = dt.datetime.now().astimezone().replace(second=0, microsecond=0)
        if not is_within_active_window(moment, args.active_hours):
            print(f"Skipping {moment:%H:%M}; outside active window", file=sys.stderr)
            continue
        print(describe_event(moment, include_hour_strike, profile))
        try:
            play_event(moment, args)
        except Exception as exc:  # pragma: no cover - operational guardrail
            print(f"Playback failed at {moment:%H:%M}: {exc}", file=sys.stderr)
    return 0


def main() -> int:
    args = parse_args()
    args.backend = resolve_backend(args.backend)

    if args.watch:
        return run_watch_mode(args)

    moment = parse_clock_time(args.time, dt.datetime.now().astimezone())
    if not is_within_active_window(moment, args.active_hours):
        print(f"Muted by active window: {moment:%H:%M}", file=sys.stderr)
        return 0

    quarter = quarter_index(moment)
    if quarter is None:
        print(
            "Choose a quarter-hour boundary such as 09:15, 09:30, 09:45, or 10:00.",
            file=sys.stderr,
        )
        return 2

    play_event(moment, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
