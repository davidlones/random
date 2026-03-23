#!/usr/bin/env python3
from __future__ import annotations

import argparse
import curses
import json
import os
import queue
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Optional

from radio_config import expand_path, load_radio_config

CONFIG = load_radio_config()
PATHS_CONFIG = CONFIG["paths"]
MONITOR_CONFIG = CONFIG["monitor"]
MONITOR_TRANSCRIBE_CONFIG = MONITOR_CONFIG["transcribe"]
REMOTE_HOST = str(PATHS_CONFIG["remote_host"])
REMOTE_PORT = int(PATHS_CONFIG["remote_port"])
XVFB_DISPLAY = str(PATHS_CONFIG["xvfb_display"])
TRANSCRIBE_SCRIPT = os.path.join(os.path.dirname(__file__), "radio_transcribe.py")
RADIO_STATE_DIR = expand_path(PATHS_CONFIG["state_dir"])
DEFAULT_DECODER_LOG = expand_path(MONITOR_CONFIG["decoder_log"])
DEFAULT_DECODER_HITS_LOG = expand_path(MONITOR_CONFIG["decoder_hits_log"])
DEFAULT_EVENT_LOG = expand_path(MONITOR_CONFIG["event_log"])
DEFAULT_TRANSCRIPT_LOG = expand_path(MONITOR_CONFIG["transcript_log"])
DEFAULT_CORTEX_STATUS = expand_path(MONITOR_CONFIG["cortex_status"])
DEFAULT_CORTEX_MANUAL_TRIGGER = expand_path(MONITOR_CONFIG["manual_inference_trigger"])
DEFAULT_DECODERS = list(MONITOR_CONFIG["decoders"])
KNOWN_DECODER_NAMES = tuple(DEFAULT_DECODERS) + (
    "FLEX",
    "UFSK1200",
    "CLIPFSK",
    "FMSFSK",
    "HAPN4800",
    "ZVEI1",
    "ZVEI2",
    "ZVEI3",
    "DZVEI",
    "PZVEI",
    "EEA",
    "EIA",
    "CCIR",
    "MORSE_CW",
    "DUMPCSV",
    "X10",
)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def clean_value(value: Optional[str], default: str = "n/a") -> str:
    text = (value or "").strip()
    return text or default


def get_gqrx_sink_input() -> Optional[tuple[str, str]]:
    try:
        output = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    current_index: Optional[str] = None
    app_name = ""
    app_binary = ""
    media_name = ""
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if raw_line.startswith("Sink Input #"):
            if current_index and (app_name == "GQRX" or app_binary == "gqrx"):
                label = media_name or app_name or app_binary or "gqrx"
                return current_index, label
            current_index = raw_line.split("#", 1)[1].strip()
            app_name = ""
            app_binary = ""
            media_name = ""
            continue
        if line.startswith("application.name ="):
            app_name = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("application.process.binary ="):
            app_binary = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("media.name ="):
            media_name = line.split("=", 1)[1].strip().strip('"')
    if current_index and (app_name == "GQRX" or app_binary == "gqrx"):
        label = media_name or app_name or app_binary or "gqrx"
        return current_index, label
    return None


def read_cortex_snapshot(path: str = DEFAULT_CORTEX_STATUS) -> dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            status = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {
            "cortex_active": "no",
            "cortex_backend": "n/a",
            "cortex_model": "n/a",
            "cortex_radio_mode": "n/a",
            "cortex_last_inference": "n/a",
            "cortex_topic": "n/a",
            "cortex_song": "n/a",
            "cortex_entity": "n/a",
            "cortex_inference_ts": "n/a",
        }

    last_inference = status.get("last_monitor_inference") or status.get("last_inference") or {}
    title = clean_value(last_inference.get("title"), "")
    artist = clean_value(last_inference.get("artist"), "")
    if title and artist:
        song = f"{title} by {artist}"
    else:
        song = title or artist or "n/a"
    return {
        "cortex_active": "yes" if status.get("active") else "no",
        "cortex_backend": clean_value(status.get("backend")),
        "cortex_model": clean_value(
            status.get("openai_model") if status.get("backend") == "openai" else status.get("model_path")
        ),
        "cortex_radio_mode": clean_value(status.get("radio_mode")),
        "cortex_last_inference": clean_value(last_inference.get("summary")),
        "cortex_topic": clean_value(last_inference.get("topic")),
        "cortex_song": song,
        "cortex_entity": clean_value(last_inference.get("entity")),
        "cortex_inference_ts": clean_value(last_inference.get("window_end") or last_inference.get("ts")),
    }


def request_manual_inference(path: str = DEFAULT_CORTEX_MANUAL_TRIGGER) -> str:
    token = now_iso()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


class MonitorEventLogger:
    def __init__(self, path: str) -> None:
        self.path = path
        self._started = False
        self._last_station_signature: Optional[tuple[str, ...]] = None
        self._last_metadata_signature: Optional[tuple[str, ...]] = None
        self._last_event_key: Optional[tuple[str, str, str, str]] = None
        self._last_event_at: float = 0.0
        self._last_metadata_update_at: float = 0.0

    def emit_if_changed(self, state: dict[str, str]) -> None:
        station_signature = (
            state["freq"],
            state["mode"],
            state["rds_pi"],
        )
        metadata_signature = (
            state["freq"],
            state["mode"],
            state["rds_pi"],
            state["ui_text"],
            state["transcribe_backend"],
            state["transcribe_model"],
        )
        if not self._started:
            self._write(
                {
                    "ts": now_iso(),
                    "type": "system",
                    "content_type": "receiver_metadata",
                    "summary": f"monitor started on {state['freq_mhz']} MHz {state['mode']}",
                    "freq_hz": state["freq"],
                    "freq_mhz": state["freq_mhz"],
                    "mode": state["mode"],
                    "metadata": state,
                }
            )
            self._started = True
            self._last_station_signature = station_signature
            self._last_metadata_signature = metadata_signature
            return

        if station_signature != self._last_station_signature:
            if state["mode"] == "offline":
                self._last_station_signature = station_signature
                self._last_metadata_signature = metadata_signature
                return
            self._write(
                {
                    "ts": now_iso(),
                    "type": "system",
                    "content_type": "station_change",
                    "summary": f"station changed to {state['freq_mhz']} MHz {state['mode']}",
                    "freq_hz": state["freq"],
                    "freq_mhz": state["freq_mhz"],
                    "mode": state["mode"],
                    "rds_pi": state["rds_pi"],
                    "ui_text": state["ui_text"],
                    "metadata": state,
                }
            )
            self._last_station_signature = station_signature
            self._last_metadata_signature = metadata_signature
            return

        if metadata_signature != self._last_metadata_signature:
            now = time.time()
            if now - self._last_metadata_update_at < 30:
                self._last_metadata_signature = metadata_signature
                return
            self._write(
                {
                    "ts": now_iso(),
                    "type": "system",
                    "content_type": "receiver_metadata_update",
                    "summary": f"receiver metadata updated on {state['freq_mhz']} MHz",
                    "freq_hz": state["freq"],
                    "freq_mhz": state["freq_mhz"],
                    "mode": state["mode"],
                    "rds_pi": state["rds_pi"],
                    "ui_text": state["ui_text"],
                    "metadata": state,
                }
            )
            self._last_metadata_signature = metadata_signature
            self._last_metadata_update_at = now

    def _write(self, payload: dict[str, object]) -> None:
        content_type = str(payload.get("content_type") or "")
        freq_mhz = str(payload.get("freq_mhz") or "")
        mode = str(payload.get("mode") or "")
        summary = str(payload.get("summary") or "")
        key = (content_type, freq_mhz, mode, summary)
        now = time.time()
        if key == self._last_event_key and now - self._last_event_at < 5:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
        self._last_event_key = key
        self._last_event_at = now


def capture_monitor_state(
    decoder: "DecoderTail",
    monitor_source: str,
    decoders: list[str],
    transcript: Optional["TranscriptTail"],
    *,
    cached_title: Optional[str],
    cached_ui_text: Optional[str],
    cortex_snapshot: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    version = clean_value(remote_command("_"), "offline")
    freq = clean_value(remote_command("f"), "offline")
    mode = get_mode_summary()
    strength = clean_value(remote_command("l STRENGTH"))
    sql = clean_value(remote_command("l SQL"))
    dsp = clean_value(remote_command("u DSP"))
    rds = clean_value(remote_command("u RDS"))
    rds_pi = clean_value(remote_command("p RDS_PI"))
    gains = get_gain_summary()
    try:
        freq_mhz = f"{float(freq) / 1_000_000:.6f}"
    except (TypeError, ValueError):
        freq_mhz = "offline"
    return {
        "version": version,
        "freq": freq,
        "freq_mhz": freq_mhz,
        "mode": mode,
        "signal": strength,
        "squelch": sql,
        "gains": gains,
        "dsp": dsp,
        "rds": rds,
        "rds_pi": rds_pi,
        "window": clean_value(cached_title),
        "ui_text": clean_value(cached_ui_text),
        "audio_source": monitor_source,
        "decoders": ", ".join(decoders),
        "decoder_log": decoder.log_path,
        "decoder_hits": decoder.hits_log_path,
        "transcription": "on" if transcript else "off",
        "transcribe_backend": transcript.effective_backend if transcript else "n/a",
        "transcribe_model": transcript.effective_model if transcript else "n/a",
        "cortex_active": clean_value((cortex_snapshot or {}).get("cortex_active")),
        "cortex_backend": clean_value((cortex_snapshot or {}).get("cortex_backend")),
        "cortex_model": clean_value((cortex_snapshot or {}).get("cortex_model")),
        "cortex_radio_mode": clean_value((cortex_snapshot or {}).get("cortex_radio_mode")),
        "cortex_last_inference": clean_value((cortex_snapshot or {}).get("cortex_last_inference")),
        "cortex_topic": clean_value((cortex_snapshot or {}).get("cortex_topic")),
        "cortex_song": clean_value((cortex_snapshot or {}).get("cortex_song")),
        "cortex_entity": clean_value((cortex_snapshot or {}).get("cortex_entity")),
        "cortex_inference_ts": clean_value((cortex_snapshot or {}).get("cortex_inference_ts")),
    }


def remote_command(command: str, timeout: float = 1.0) -> Optional[str]:
    try:
        with socket.create_connection((REMOTE_HOST, REMOTE_PORT), timeout=timeout) as sock:
            sock.sendall((command + "\n").encode())
            data = sock.recv(4096)
    except OSError:
        return None
    if not data:
        return None
    return data.decode(errors="replace").strip()


def get_window_title() -> Optional[str]:
    try:
        result = subprocess.run(
            ["xdotool", "search", "--name", "Gqrx"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "DISPLAY": XVFB_DISPLAY},
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not ids:
        return None
    names = []
    for wid in ids:
        try:
            name = subprocess.run(
                ["xdotool", "getwindowname", wid],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "DISPLAY": XVFB_DISPLAY},
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        if name:
            names.append(name)
    if not names:
        return None
    preferred = [name for name in names if name.lower() != "gqrx"]
    if preferred:
        return max(preferred, key=len)
    return names[0]


def get_ui_text() -> Optional[str]:
    try:
        result = subprocess.run(
            ["xdotool", "search", "--name", "Gqrx"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "DISPLAY": XVFB_DISPLAY},
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not ids:
        return None
    window_id = ids[-1]
    base = "/tmp/radio-monitor-ui"
    try:
        subprocess.run(
            ["import", "-window", window_id, f"{base}.png"],
            check=True,
            capture_output=True,
            env={**os.environ, "DISPLAY": XVFB_DISPLAY},
        )
        subprocess.run(
            [
                "convert",
                f"{base}.png",
                "-crop",
                "778x140+0+0",
                "-colorspace",
                "Gray",
                "-resize",
                "200%",
                "-threshold",
                "55%",
                f"{base}-top.png",
            ],
            check=True,
            capture_output=True,
        )
        ocr = subprocess.run(
            ["tesseract", f"{base}-top.png", "stdout", "--psm", "6"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    lines = []
    for raw in ocr.splitlines():
        line = " ".join(raw.split())
        if len(line) < 4:
            continue
        if line.lower().startswith("warning:"):
            continue
        lines.append(line)
    if not lines:
        return None
    return " | ".join(lines[:3])


def get_gain_summary() -> str:
    names = remote_command("l ?")
    if not names:
        return "n/a"
    bits = []
    for name in names.split():
        if name in {"SQL", "STRENGTH"}:
            continue
        value = remote_command(f"l {name}")
        if value is not None:
            bits.append(f"{name}={value}")
    return ", ".join(bits) if bits else "n/a"


def get_mode_summary() -> str:
    mode = remote_command("m")
    if not mode:
        return "offline"
    bits = [part.strip() for part in mode.splitlines() if part.strip()]
    return " / ".join(bits) if bits else "offline"


def get_default_monitor_source() -> Optional[str]:
    try:
        info = subprocess.run(
            ["pactl", "info"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    default_sink = None
    for line in info.splitlines():
        if line.startswith("Default Sink:"):
            default_sink = line.split(":", 1)[1].strip()
            break
    if not default_sink:
        return None
    return f"{default_sink}.monitor"


def get_default_capture_target() -> Optional[str]:
    sink_input = get_gqrx_sink_input()
    if sink_input is not None:
        return f"sink-input:{sink_input[0]}"
    return get_default_monitor_source()


class DecoderTail:
    def __init__(self, source: str, decoders: list[str], log_path: str, hits_log_path: str) -> None:
        self.source = source
        self.decoders = decoders
        self.log_path = log_path
        self.hits_log_path = hits_log_path
        self.lines: deque[str] = deque(maxlen=200)
        self.errors: deque[str] = deque(maxlen=20)
        self.last_line: str = ""
        self.last_hit: str = ""
        self.line_count = 0
        self.hit_count = 0
        self.started_at = time.time()
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._proc: Optional[subprocess.Popen[str]] = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._thread.join(timeout=1)

    def poll(self) -> None:
        while True:
            try:
                line = self._queue.get_nowait()
            except queue.Empty:
                return
            self.line_count += 1
            self.last_line = line
            self.lines.append(line)

    def _append_log(self, path: str, line: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {line}\n")

    def _is_likely_hit(self, line: str) -> bool:
        normalized = line.strip()
        if not normalized:
            return False
        lowered = normalized.lower()
        if "decoder session start" in lowered or "decoder exited" in lowered:
            return False
        if not any(name in normalized for name in KNOWN_DECODER_NAMES):
            return False

        payload = re.split(r"\b(?:%s)\b[: ]*" % "|".join(re.escape(name) for name in KNOWN_DECODER_NAMES), normalized, maxsplit=1)
        tail = payload[-1] if payload else normalized
        alnum_chars = sum(ch.isalnum() for ch in tail)
        compact_tail = tail.strip(" :-")
        if alnum_chars >= 4:
            return True
        if any(name in normalized for name in ("DTMF", "ZVEI1", "ZVEI2", "ZVEI3", "DZVEI", "PZVEI")) and compact_tail:
            return True
        return False

    def _run(self) -> None:
        decoders = " ".join(f"-a {shlex.quote(name)}" for name in self.decoders)
        parec_cmd = ["parec"]
        if self.source.startswith("sink-input:"):
            parec_cmd.extend(["--monitor-stream", self.source.split(":", 1)[1]])
        else:
            parec_cmd.extend(["--device", self.source])
        parec_cmd.extend(
            [
                "--format=s16le",
                "--rate=22050",
                "--channels=1",
                "--latency-msec=100",
            ]
        )
        self._append_log(
            self.log_path,
            f"--- decoder session start source={self.source} decoders={','.join(self.decoders)} ---"
        )
        self._append_log(
            self.hits_log_path,
            f"--- decoder hit session start source={self.source} decoders={','.join(self.decoders)} ---"
        )
        multimon_cmd = f"multimon-ng -t raw --timestamp --label RADIO -v 3 {decoders} -"
        shell_cmd = f"{' '.join(shlex.quote(part) for part in parec_cmd)} | {multimon_cmd}"
        try:
            self._proc = subprocess.Popen(
                shell_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.errors.append(f"decoder start failed: {exc}")
            return

        assert self._proc.stdout is not None
        for raw_line in self._proc.stdout:
            if self._stop.is_set():
                break
            line = raw_line.strip()
            if line:
                self._append_log(self.log_path, line)
                if self._is_likely_hit(line):
                    self.hit_count += 1
                    self.last_hit = line
                    self._append_log(self.hits_log_path, line)
                self._queue.put(line)

        if self._proc.poll() not in (0, None) and not self._stop.is_set():
            message = f"decoder exited with {self._proc.returncode}"
            self.errors.append(message)
            self._append_log(self.log_path, message)
            self._append_log(self.hits_log_path, message)


class TranscriptTail:
    def __init__(
        self,
        source: str,
        *,
        backend: str,
        openai_model: str,
        chunk_seconds: float,
        all_local: bool = False,
        transcript_log_path: str = DEFAULT_TRANSCRIPT_LOG,
    ) -> None:
        self.source = source
        self.backend = backend
        self.openai_model = openai_model
        self.chunk_seconds = chunk_seconds
        self.all_local = all_local
        self.transcript_log_path = transcript_log_path
        self.effective_backend = backend
        self.effective_model = openai_model
        self.lines: deque[str] = deque(maxlen=100)
        self.errors: deque[str] = deque(maxlen=20)
        self.last_partial: str = ""
        self.last_final: str = ""
        self.started_at = time.time()
        self.last_event_at: Optional[float] = None
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._proc: Optional[subprocess.Popen[str]] = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._thread.join(timeout=1)

    def poll(self) -> None:
        while True:
            try:
                payload = self._queue.get_nowait()
            except queue.Empty:
                return
            if payload.get("type") == "error":
                message = payload.get("message", "").strip()
                if message:
                    self.errors.append(message)
                continue
            if payload.get("event") == "backend_status":
                self.effective_backend = str(payload.get("backend") or self.effective_backend)
                self.effective_model = str(payload.get("model") or self.effective_model)
                continue

            event = payload.get("event", "").strip()
            text = payload.get("text", "").strip()
            if not text:
                continue

            self.last_event_at = time.time()
            if event == "partial":
                self.last_partial = text
                self.lines.append(f"partial: {text}")
                continue

            if event == "final":
                self.last_final = text
                self.last_partial = ""
                self.lines.append(f"final: {text}")
                self._append_transcript(payload)
                continue

            self.lines.append(text)

    def _append_transcript(self, payload: dict[str, object]) -> None:
        record = {
            "event": "final",
            "text": str(payload.get("text") or "").strip(),
            "ts": str(payload.get("ts") or payload.get("timestamp") or now_iso()),
            "source_name": "RADIO",
            "source": "monitor",
            "backend": self.effective_backend,
            "model": self.effective_model,
            "audio_source": self.source,
        }
        if not record["text"]:
            return
        path = self.transcript_log_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")

    def _run(self) -> None:
        cmd = [
            sys.executable,
            TRANSCRIBE_SCRIPT,
            "--source",
            self.source,
            "--backend",
            self.backend,
            "--chunk-seconds",
            str(self.chunk_seconds),
            "--json",
        ]
        if self.all_local:
            cmd.append("--all-local")
        if self.backend == "openai":
            cmd.extend(["--openai-model", self.openai_model])
        elif self.backend == "nemo":
            cmd.extend(["--nemo-model", self.openai_model])
        if self.backend == "vosk":
            cmd.append("--partials")
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.errors.append(f"transcriber start failed: {exc}")
            return

        assert self._proc.stdout is not None
        for raw_line in self._proc.stdout:
            if self._stop.is_set():
                break
            line = raw_line.strip()
            if line:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    payload = {"type": "error", "message": f"unparsed transcriber output: {line}"}
                self._queue.put(payload)

        if self._proc.poll() not in (0, None) and not self._stop.is_set():
            self.errors.append(f"transcriber exited with {self._proc.returncode}")


def draw_screen(
    stdscr: "curses._CursesWindow",
    decoder: DecoderTail,
    transcript: Optional[TranscriptTail],
    monitor_source: str,
    decoders: list[str],
    event_logger: MonitorEventLogger,
) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(250)

    last_ui_poll = 0.0
    cached_ui_text = None
    cached_title = None
    cached_cortex_snapshot = read_cortex_snapshot()
    ui_notice = ""
    ui_notice_until = 0.0
    while True:
        decoder.poll()
        if transcript:
            transcript.poll()
        height, width = stdscr.getmaxyx()
        stdscr.erase()

        now = time.time()
        if now - last_ui_poll > 5:
            cached_title = get_window_title()
            cached_ui_text = get_ui_text()
            cached_cortex_snapshot = read_cortex_snapshot()
            last_ui_poll = now
        state = capture_monitor_state(
            decoder,
            monitor_source,
            decoders,
            transcript,
            cached_title=cached_title,
            cached_ui_text=cached_ui_text,
            cortex_snapshot=cached_cortex_snapshot,
        )
        event_logger.emit_if_changed(state)

        status_lines = [
            "Radio Monitor",
            f"version: {state['version']}",
            f"freq: {state['freq_mhz']} MHz ({state['freq']} Hz)",
            f"mode: {state['mode']}",
            f"signal: {state['signal']} dBFS",
            f"squelch: {state['squelch']} dBFS",
            f"gains: {state['gains']}",
            f"dsp: {state['dsp']}",
            f"rds: {state['rds']}",
            f"rds_pi: {state['rds_pi']}",
            f"window: {state['window']}",
            f"ui_text: {state['ui_text']}",
            f"audio source: {state['audio_source']}",
            f"decoders: {', '.join(decoders)}",
            f"decoder log: {decoder.log_path}",
            f"decoder hits: {decoder.hits_log_path}",
            f"decoder lines: {decoder.line_count}",
            f"likely hits: {decoder.hit_count}",
            f"last hit: {decoder.last_hit or 'n/a'}",
            f"transcription: {'on' if transcript else 'off'}",
            f"transcribe backend: {state['transcribe_backend']}",
            f"transcribe model: {state['transcribe_model']}",
            f"cortex: {state['cortex_active']}",
            f"cortex backend: {state['cortex_backend']}",
            f"cortex model: {state['cortex_model']}",
            f"cortex mode: {state['cortex_radio_mode']}",
            f"last inferred: {state['cortex_last_inference']}",
            f"inferred topic: {state['cortex_topic']}",
            f"inferred song: {state['cortex_song']}",
            f"inferred entity: {state['cortex_entity']}",
            f"inference as of: {state['cortex_inference_ts']}",
            f"action: {ui_notice or 'n/a'}",
            "keys: q quit, c clear decoded lines, i infer now",
            "",
        ]

        for idx, line in enumerate(status_lines[: height - 1]):
            stdscr.addnstr(idx, 0, line, width - 1)

        body_start = min(len(status_lines), height - 1)
        remaining = max(0, height - body_start - 1)

        if transcript:
            transcript_block = max(3, remaining // 2) if remaining > 6 else max(0, remaining - 3)
            decoder_block = max(0, remaining - transcript_block - 2)
            transcript_lines: list[str] = []
            transcript_status = "waiting for recognizable speech"
            if transcript._proc is not None and transcript._proc.poll() is not None:
                transcript_status = f"stopped (exit {transcript._proc.returncode})"
            elif transcript.last_event_at is not None:
                age = max(0, int(time.time() - transcript.last_event_at))
                transcript_status = f"active ({age}s since last event)"
            elif time.time() - transcript.started_at > 5:
                transcript_status = "running, but nothing recognized yet"

            transcript_lines.append(f"status: {transcript_status}")
            if transcript.last_partial:
                transcript_lines.append(f"live: {transcript.last_partial}")
            elif transcript.last_final:
                transcript_lines.append(f"last final: {transcript.last_final}")
            else:
                transcript_lines.append("live: (no speech recognized yet; music/noise will often stay blank)")

            transcript_lines.extend(list(transcript.lines))
            if transcript.errors:
                transcript_lines.extend(f"[error] {line}" for line in transcript.errors)
            transcript_content = transcript_lines[-max(0, transcript_block - 1):]
            if transcript_block > 0:
                stdscr.addnstr(body_start, 0, "Transcript:", width - 1)
                for idx, line in enumerate(transcript_content[: max(0, transcript_block - 1)]):
                    stdscr.addnstr(body_start + 1 + idx, 0, line, width - 1)
            decoder_start = body_start + transcript_block
        else:
            decoder_block = remaining
            decoder_start = body_start

        if decoder_block > 0:
            stdscr.addnstr(decoder_start, 0, "Decoded Lines:", width - 1)
            content = list(decoder.lines)
            if decoder.errors:
                content.extend(f"[error] {line}" for line in decoder.errors)
            content = content[-max(0, decoder_block - 1):]
            if not content and decoder_block > 1:
                content = [
                    "(no decoder lines yet; raw output is still being appended to the decoder log)"
                ]
            for idx, line in enumerate(content[: max(0, decoder_block - 1)]):
                stdscr.addnstr(decoder_start + 1 + idx, 0, line, width - 1)

        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            break
        if ch == ord("c"):
            decoder.lines.clear()
            decoder.errors.clear()
            if transcript:
                transcript.lines.clear()
                transcript.errors.clear()
            ui_notice = "cleared local monitor buffers"
            ui_notice_until = time.time() + 4
        if ch == ord("i"):
            try:
                token = request_manual_inference()
                ui_notice = f"manual inference requested at {token}"
            except OSError as exc:
                ui_notice = f"manual inference request failed: {exc}"
            ui_notice_until = time.time() + 6
        if ui_notice and time.time() >= ui_notice_until:
            ui_notice = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive digital-data monitor for the headless radio.")
    parser.add_argument(
        "--source",
        help="PulseAudio monitor source or sink-input target like sink-input:3321. Defaults to the live GQRX stream when available, else the default sink monitor.",
    )
    parser.add_argument(
        "--decoders",
        help="Comma-separated multimon-ng decoders. Defaults to a small general-purpose set.",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Print one snapshot of receiver state and exit.",
    )
    parser.add_argument(
        "--transcribe",
        action="store_true",
        default=bool(MONITOR_TRANSCRIBE_CONFIG["enabled"]),
        help="Run live speech transcription beside the digital decoder feed.",
    )
    parser.add_argument(
        "--all-local",
        action="store_true",
        default=bool(MONITOR_TRANSCRIBE_CONFIG["all_local"]),
        help="Force local-only mode for live transcription. Prefers NeMo, then Vosk.",
    )
    parser.add_argument(
        "--transcribe-backend",
        choices=("auto", "vosk", "openai", "nemo"),
        default=str(MONITOR_TRANSCRIBE_CONFIG["backend"]),
        help="Transcription backend to use when --transcribe is enabled.",
    )
    parser.add_argument(
        "--transcribe-model",
        default=str(MONITOR_TRANSCRIBE_CONFIG["model"]),
        help="Transcription model to use when the selected backend accepts a model name.",
    )
    parser.add_argument(
        "--transcribe-chunk-seconds",
        type=float,
        default=float(MONITOR_TRANSCRIBE_CONFIG["chunk_seconds"]),
        help="Chunk length for the OpenAI transcription backend.",
    )
    parser.add_argument(
        "--decoder-log",
        default=DEFAULT_DECODER_LOG,
        help="Append all decoder output lines to this file.",
    )
    parser.add_argument(
        "--decoder-hits-log",
        default=DEFAULT_DECODER_HITS_LOG,
        help="Append lines that look like possible valid decodes to this file.",
    )
    parser.add_argument(
        "--event-log",
        default=DEFAULT_EVENT_LOG,
        help="Append monitor metadata and station-change events to this JSONL file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source or get_default_capture_target()
    if not source:
        raise SystemExit("could not determine radio audio capture target")

    decoders = list(DEFAULT_DECODERS)
    if args.decoders:
        decoders = [item.strip() for item in args.decoders.split(",") if item.strip()]

    if args.snapshot:
        print(f"version={remote_command('_') or 'offline'}")
        print(f"freq={remote_command('f') or 'offline'}")
        print(f"mode={get_mode_summary()}")
        print(f"signal={remote_command('l STRENGTH') or 'n/a'}")
        print(f"squelch={remote_command('l SQL') or 'n/a'}")
        print(f"gains={get_gain_summary()}")
        print(f"dsp={remote_command('u DSP') or 'n/a'}")
        print(f"rds={remote_command('u RDS') or 'n/a'}")
        print(f"rds_pi={remote_command('p RDS_PI') or 'n/a'}")
        print(f"window={get_window_title() or 'n/a'}")
        print(f"ui_text={get_ui_text() or 'n/a'}")
        print(f"audio_source={source}")
        print(f"decoders={','.join(decoders)}")
        print(f"transcription={'on' if args.transcribe else 'off'}")
        cortex = read_cortex_snapshot()
        print(f"cortex_active={cortex['cortex_active']}")
        print(f"cortex_backend={cortex['cortex_backend']}")
        print(f"cortex_model={cortex['cortex_model']}")
        print(f"cortex_last_inference={cortex['cortex_last_inference']}")
        print(f"cortex_topic={cortex['cortex_topic']}")
        print(f"cortex_song={cortex['cortex_song']}")
        print(f"cortex_entity={cortex['cortex_entity']}")
        return 0

    decoder = DecoderTail(source, decoders, args.decoder_log, args.decoder_hits_log)
    event_logger = MonitorEventLogger(args.event_log)
    transcript = (
        TranscriptTail(
            source,
            backend=args.transcribe_backend,
            openai_model=args.transcribe_model,
            chunk_seconds=args.transcribe_chunk_seconds,
            all_local=args.all_local,
        )
        if args.transcribe
        else None
    )
    decoder.start()
    if transcript:
        transcript.start()
    try:
        curses.wrapper(draw_screen, decoder, transcript, source, decoders, event_logger)
    finally:
        decoder.stop()
        if transcript:
            transcript.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
