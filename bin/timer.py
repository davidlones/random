#!/usr/bin/env python3

import time
import sys
import argparse
import signal
import math
import wave
import struct
import tempfile
import subprocess
import os
from datetime import datetime, timedelta

# -----------------------------
# Audio synthesis configuration
# -----------------------------

SAMPLE_RATE = 44100


def generate_tone(frequency, duration, volume=0.6, fade=0.02):
    total_samples = int(SAMPLE_RATE * duration)
    fade_samples = int(SAMPLE_RATE * fade)
    frames = []

    for i in range(total_samples):
        t = i / SAMPLE_RATE
        sample = math.sin(2 * math.pi * frequency * t)

        # Envelope to avoid clicks
        if i < fade_samples:
            sample *= i / fade_samples
        elif i > total_samples - fade_samples:
            sample *= (total_samples - i) / fade_samples

        frames.append(sample * volume)

    return frames


def play_samples(samples):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
        filename = f.name

    with wave.open(filename, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)

        for s in samples:
            wf.writeframes(struct.pack("<h", int(s * 32767)))

    try:
        if sys.platform.startswith("darwin"):
            subprocess.run(["afplay", filename], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(filename)
        else:
            subprocess.run(["aplay", filename], check=False)
    finally:
        os.unlink(filename)


def alarm():
    # A simple, recognizable audio signature
    sequence = [
        (880, 0.03),
        (660, 0.05),
        (880, 0.03),
    ]

    samples = []
    for freq, dur in sequence:
        samples.extend(generate_tone(freq, dur))
        samples.extend(generate_tone(0, 0.05))  # silence gap

    play_samples(samples)


# -----------------------------
# Timer logic
# -----------------------------

class CLITimer:
    def __init__(self, duration, message=None, interval=None):
        self.duration = duration
        self.message = message or "Time’s up."
        self.interval = interval
        self._running = True

        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, signum, frame):
        print("\nTimer interrupted.")
        sys.exit(0)

    @staticmethod
    def format_time(seconds):
        return str(timedelta(seconds=seconds))

    def run(self):
        start = datetime.now()
        end = start + timedelta(seconds=self.duration)

        print(f"Timer started for {self.format_time(self.duration)}")
        print(f"Ends at {end.strftime('%H:%M:%S')}\n")

        last_interval = 0

        while self._running:
            remaining = int((end - datetime.now()).total_seconds())
            if remaining <= 0:
                break

            print(
                f"\rRemaining: {self.format_time(remaining)}",
                end="",
                flush=True,
            )

            if self.interval:
                elapsed = self.duration - remaining
                if elapsed // self.interval > last_interval:
                    last_interval += 1
                    print(
                        f"\nCheckpoint: {self.format_time(elapsed)} elapsed"
                    )
                    play_samples(generate_tone(440, 0.15))

            time.sleep(1)

        print("\n")
        alarm()
        print(self.message)


# -----------------------------
# CLI interface
# -----------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="A deceptively simple CLI timer with real audio"
    )
    parser.add_argument(
        "seconds",
        type=int,
        help="Duration of the timer in seconds",
    )
    parser.add_argument(
        "-m",
        "--message",
        type=str,
        help="Message to display when the timer ends",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=int,
        help="Checkpoint interval in seconds (plays a soft tone)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    timer = CLITimer(
        duration=args.seconds,
        message=args.message,
        interval=args.interval,
    )
    timer.run()
