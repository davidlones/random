#!/usr/bin/env python3
"""Socket-controlled reminder daemon with GUI dismiss button and 11speak output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import select
import socket
import subprocess
import sys
import threading
import tempfile
import time
import tkinter as tk
import uuid
from pathlib import Path
import re
from typing import Any

DEFAULT_MESSAGE = "If anyone is in there, David is outside... somewhere"
DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_PRESENCE_POLL_SECONDS = 2.0
MAX_HISTORY_ITEMS = 200
HISTORY_PATH = Path("/home/david/.local/state/custom_notify/history.json")
AUDIO_CACHE_DIR = Path("/home/david/.cache/custom_notify/audio")
ELEVEN_SPEAK_SCRIPT = Path("/home/david/random/bin/11speak.py")
SOCKET_PATH = Path("/tmp/clint_outside_notifier.sock")
PID_PATH = Path("/tmp/clint_outside_notifier.pid")
HOME_ASSISTANT_URL = "https://ha.system42.one"

BG = "#101418"
PANEL_BG = "#151b22"
TEXT = "#e6edf3"
MUTED = "#9fb0c0"
ENTRY_BG = "#0d1117"
ACCENT = "#2f81f7"
ACCENT_ACTIVE = "#1f6feb"
BUTTON_FG = "#ffffff"
DEFAULT_WINDOW_SIZE = "760x280"
DEFAULT_WINDOW_WIDTH = 760
DEFAULT_WINDOW_HEIGHT = 280
DEFAULT_WINDOW_POSITION = "center"
DEFAULT_WINDOW_ANIMATION = "smooth"
DEFAULT_WINDOW_ANIMATION_DURATION_MS = 220
WINDOW_POSITION_PRESETS = {
    "center",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
}
WINDOW_ANIMATION_PRESETS = {"off", "side-bounce", "smooth"}
WINDOW_EDGE_MARGIN = 24


def format_timestamp(ts_unix: float) -> str:
    local = time.localtime(ts_unix)
    month = local.tm_mon
    day = local.tm_mday
    year = local.tm_year
    hour24 = local.tm_hour
    minute = local.tm_min
    second = local.tm_sec
    ampm = "AM" if hour24 < 12 else "PM"
    hour12 = hour24 % 12
    if hour12 == 0:
        hour12 = 12
    return f"[{month}/{day}/{year} {hour12}:{minute:02d}:{second:02d} {ampm}]"


def load_home_assistant_token() -> str:
    token = os.environ.get("HOME_ASSISTANT_TOKEN", "").strip()
    if token:
        return token

    bashrc_path = Path.home() / ".bashrc"
    if not bashrc_path.exists():
        return ""
    try:
        for raw in reversed(bashrc_path.read_text(encoding="utf-8").splitlines()):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(
                r'^export\s+HOME_ASSISTANT_TOKEN\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s#]+))\s*$',
                line,
            )
            if not match:
                continue
            for group in match.groups():
                if group:
                    return group.strip()
    except OSError:
        return ""
    return ""


def get_entity_state_via_hass_cli(entity_id: str, token: str) -> str | None:
    if not token.strip():
        return None
    cmd = [
        "hass-cli",
        "--token",
        token,
        "-s",
        HOME_ASSISTANT_URL,
        "-o",
        "json",
        "state",
        "get",
        entity_id,
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    state = first.get("state")
    if state is None:
        return None
    return str(state)


def option_was_provided(option: str, argv: list[str] | None = None) -> bool:
    tokens = argv if argv is not None else sys.argv[1:]
    for token in tokens:
        if token == option or token.startswith(f"{option}="):
            return True
    return False


def resolve_message_text(text: str, presence_change_enabled: bool, presence_entity: str) -> str:
    raw = text.strip()
    if raw:
        return raw
    if presence_change_enabled and presence_entity.strip():
        return ""
    return DEFAULT_MESSAGE


def speak_text_once_local(text: str) -> None:
    spoken = str(text or "").strip()
    if not spoken:
        return
    cmd = ["python3", str(ELEVEN_SPEAK_SCRIPT), spoken]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"11speak exited with code {result.returncode}")


def normalize_window_position(value: str) -> str:
    raw = str(value or "").strip().lower()
    return raw or DEFAULT_WINDOW_POSITION


def window_position_help_text() -> str:
    presets = ", ".join(sorted(WINDOW_POSITION_PRESETS))
    return f"Window placement: one of {presets}, or coordinates like 120,80"


def validate_window_position(value: str) -> str:
    normalized = normalize_window_position(value)
    if normalized in WINDOW_POSITION_PRESETS:
        return normalized
    if re.fullmatch(r"\s*-?\d+\s*,\s*-?\d+\s*", normalized):
        return normalized
    supported = ", ".join(sorted(WINDOW_POSITION_PRESETS))
    raise ValueError(
        f"invalid window position '{value}'. Use one of {supported}, or X,Y coordinates"
    )


def validate_window_animation(value: str) -> str:
    normalized = str(value or "").strip().lower() or DEFAULT_WINDOW_ANIMATION
    if normalized in WINDOW_ANIMATION_PRESETS:
        return normalized
    supported = ", ".join(sorted(WINDOW_ANIMATION_PRESETS))
    raise ValueError(f"invalid window animation '{value}'. Use one of {supported}")


def parse_window_geometry(geometry: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)([-+]\d+)([-+]\d+)", geometry.strip())
    if not match:
        raise ValueError(f"invalid geometry '{geometry}'")
    return tuple(int(group) for group in match.groups())  # type: ignore[return-value]


def get_side_orbit_points(root: tk.Tk, width: int, height: int) -> list[tuple[str, tuple[int, int]]]:
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    center_x = max((screen_width - width) // 2, 0)
    center_y = max((screen_height - height) // 2, 0)
    return [
        ("top-center", (center_x, WINDOW_EDGE_MARGIN)),
        ("right-center", (max(screen_width - width - WINDOW_EDGE_MARGIN, 0), center_y)),
        ("bottom-center", (center_x, max(screen_height - height - WINDOW_EDGE_MARGIN, 0))),
        ("left-center", (WINDOW_EDGE_MARGIN, center_y)),
    ]


def nearest_side_orbit_index(root: tk.Tk, width: int, height: int, x_coord: int, y_coord: int) -> int:
    best_index = 0
    best_distance: float | None = None
    for index, (_, point) in enumerate(get_side_orbit_points(root, width, height)):
        distance = ((point[0] - x_coord) ** 2) + ((point[1] - y_coord) ** 2)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_index = index
    return best_index


def nearest_side_orbit_label(root: tk.Tk, width: int, height: int, x_coord: int, y_coord: int) -> str:
    points = get_side_orbit_points(root, width, height)
    return points[nearest_side_orbit_index(root, width, height, x_coord, y_coord)][0]


def target_side_orbit_index(
    root: tk.Tk,
    width: int,
    height: int,
    position: str,
    target_x: int,
    target_y: int,
) -> int:
    normalized = normalize_window_position(position)
    if normalized in {"top-left", "top-right"}:
        return 0
    if normalized in {"bottom-left", "bottom-right"}:
        return 2
    return nearest_side_orbit_index(root, width, height, target_x, target_y)


def build_side_bounce_waypoints(
    root: tk.Tk,
    width: int,
    height: int,
    current_x: int,
    current_y: int,
    target_position: str,
    target_x: int,
    target_y: int,
) -> list[tuple[int, int]]:
    orbit_points = [point for _, point in get_side_orbit_points(root, width, height)]
    start_index = nearest_side_orbit_index(root, width, height, current_x, current_y)
    target_index = target_side_orbit_index(root, width, height, target_position, target_x, target_y)
    waypoints: list[tuple[int, int]] = []
    index = target_index
    while True:
        waypoints.append(orbit_points[index])
        index = (index + 1) % len(orbit_points)
        if index == target_index:
            break

    deduped: list[tuple[int, int]] = []
    if orbit_points[start_index] == orbit_points[target_index]:
        deduped.append(orbit_points[target_index])
    for point in waypoints:
        if not deduped or deduped[-1] != point:
            deduped.append(point)
    return deduped


def set_live_window_position(window_position: str, *, required: bool = False) -> bool:
    normalized = validate_window_position(window_position)
    response = send_command({"cmd": "set_window_position", "window_position": normalized})
    if response.get("ok"):
        return True

    error = str(response.get("error") or "")
    if error == "unknown cmd: set_window_position":
        if normalized == DEFAULT_WINDOW_POSITION and not required:
            return True
        print(
            "Running daemon does not support --window-position yet. "
            "Restart it with `stop` then `start --window-position ...`."
        )
        return False

    print(f"Failed to set window position: {response}")
    return False


def set_live_window_animation(window_animation: str, duration_ms: int) -> bool:
    try:
        animation = validate_window_animation(window_animation)
    except ValueError as exc:
        print(f"Invalid --window-animation: {exc}")
        return False
    response = send_command(
        {
            "cmd": "set_window_animation",
            "window_animation": animation,
            "window_animation_duration_ms": max(int(duration_ms), 0),
        }
    )
    if response.get("ok"):
        return True

    error = str(response.get("error") or "")
    if error == "unknown cmd: set_window_animation":
        if (
            animation == DEFAULT_WINDOW_ANIMATION
            and int(duration_ms) == DEFAULT_WINDOW_ANIMATION_DURATION_MS
        ):
            return True
        print(
            "Running daemon does not support window animation controls yet. "
            "Restart it with `stop` then `start --window-animation ...`."
        )
        return False

    print(f"Failed to set window animation: {response}")
    return False


def resolve_window_coordinates(root: tk.Tk, position: str, width: int, height: int) -> tuple[int, int]:
    normalized = normalize_window_position(position)
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    if normalized in WINDOW_POSITION_PRESETS:
        center_x = max((screen_width - width) // 2, 0)
        center_y = max((screen_height - height) // 2, 0)
        if normalized == "center":
            return (center_x, center_y)
        if normalized == "top-left":
            return (WINDOW_EDGE_MARGIN, WINDOW_EDGE_MARGIN)
        if normalized == "top-right":
            return (max(screen_width - width - WINDOW_EDGE_MARGIN, 0), WINDOW_EDGE_MARGIN)
        if normalized == "bottom-left":
            return (WINDOW_EDGE_MARGIN, max(screen_height - height - WINDOW_EDGE_MARGIN, 0))
        return (
            max(screen_width - width - WINDOW_EDGE_MARGIN, 0),
            max(screen_height - height - WINDOW_EDGE_MARGIN, 0),
        )

    match = re.fullmatch(r"\s*(-?\d+)\s*,\s*(-?\d+)\s*", normalized)
    if not match:
        supported = ", ".join(sorted(WINDOW_POSITION_PRESETS))
        raise ValueError(
            f"invalid window position '{position}'. Use one of {supported}, or X,Y coordinates"
        )

    x_coord = int(match.group(1))
    y_coord = int(match.group(2))
    max_x = max(screen_width - width, 0)
    max_y = max(screen_height - height, 0)
    return (min(max(x_coord, 0), max_x), min(max(y_coord, 0), max_y))


class NotifierDaemon:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("SOL Notification")
        self.window_position = DEFAULT_WINDOW_POSITION
        self.window_animation = DEFAULT_WINDOW_ANIMATION
        self.window_animation_duration_ms = DEFAULT_WINDOW_ANIMATION_DURATION_MS
        self.window_animation_after_id: str | None = None
        self.window_animation_stopped_by_click = False
        self.root.geometry(DEFAULT_WINDOW_SIZE)
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.withdraw()

        self.command_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stop_event = threading.Event()
        self.speak_thread: threading.Thread | None = None
        self.current_message = ""
        self.current_subtext = ""
        self.current_interval = DEFAULT_INTERVAL_SECONDS
        self.current_interval_enabled = True
        self.current_presence_entity = ""
        self.current_presence_change_enabled = False
        self.current_presence_poll_seconds = DEFAULT_PRESENCE_POLL_SECONDS
        self.current_presence_value = ""
        self.current_cycle = 0
        self.current_persist = False
        self.reminder_active = False
        self.active_request_id = ""
        self.responses: dict[str, dict[str, Any]] = {}
        self.dismissed_requests: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.history_lock = threading.Lock()
        self.current_audio_file: Path | None = None
        self.server_socket: socket.socket | None = None
        self.shutdown_event = threading.Event()
        self.history_path = HISTORY_PATH
        self.home_assistant_token = load_home_assistant_token()

        self.load_history()

        self.message_label = tk.Label(
            self.root,
            text="No active reminder.",
            font=("Helvetica", 12),
            justify="center",
            wraplength=700,
            bg=BG,
            fg=TEXT,
        )
        self.message_label.pack(pady=(20, 12), padx=16)

        self.subtext_label = tk.Label(
            self.root,
            text="",
            font=("Helvetica", 10),
            justify="center",
            wraplength=700,
            bg=BG,
            fg=MUTED,
        )
        self.subtext_label.pack(pady=(0, 8), padx=16)

        self.status_var = tk.StringVar(value="Idle")
        self.status_label = tk.Label(
            self.root,
            textvariable=self.status_var,
            font=("Helvetica", 10),
            bg=BG,
            fg=MUTED,
        )
        self.status_label.pack(pady=(2, 12))

        reply_frame = tk.Frame(self.root)
        reply_frame.pack(padx=16, pady=(0, 10), fill="x")
        reply_frame.configure(bg=PANEL_BG, highlightthickness=1, highlightbackground="#242c36")
        reply_frame.grid_columnconfigure(1, weight=1)
        tk.Label(reply_frame, text="Response:", bg=PANEL_BG, fg=TEXT).grid(
            row=0, column=0, sticky="w", padx=(12, 8), pady=12
        )
        self.response_var = tk.StringVar(value="")
        self.response_entry = tk.Entry(
            reply_frame,
            textvariable=self.response_var,
            width=64,
            bg=ENTRY_BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#2b3440",
            highlightcolor=ACCENT,
        )
        self.response_entry.grid(row=0, column=1, padx=(0, 10), pady=12, sticky="ew")
        send_button = tk.Button(
            reply_frame,
            text="Send Response",
            command=self.send_response_from_ui,
            width=16,
            bg=ACCENT,
            fg=BUTTON_FG,
            activebackground=ACCENT_ACTIVE,
            activeforeground=BUTTON_FG,
            relief="flat",
            bd=0,
            padx=8,
            pady=6,
        )
        send_button.grid(row=0, column=2, sticky="e", padx=(0, 12), pady=12)

        controls = tk.Frame(self.root)
        controls.configure(bg=BG)
        controls.pack(pady=(2, 8))
        dismiss = tk.Button(
            controls,
            text="Dismiss",
            command=self.dismiss,
            width=12,
            height=1,
            bg="#30363d",
            fg=TEXT,
            activebackground="#3d444d",
            activeforeground=TEXT,
            relief="flat",
            bd=0,
        )
        dismiss.grid(row=0, column=0, padx=6)

        self.root.protocol("WM_DELETE_WINDOW", self.dismiss)
        self.root.bind("<Button-1>", self.on_window_click, add="+")

    def apply_window_position(self, position: str) -> None:
        normalized = normalize_window_position(position)
        self.root.update_idletasks()
        width = max(self.root.winfo_width(), self.root.winfo_reqwidth(), DEFAULT_WINDOW_WIDTH)
        height = max(self.root.winfo_height(), self.root.winfo_reqheight(), DEFAULT_WINDOW_HEIGHT)
        x_coord, y_coord = resolve_window_coordinates(self.root, normalized, width, height)
        if self.window_animation == "side-bounce":
            self.window_animation_stopped_by_click = False
        self.animate_window_geometry(width, height, normalized, x_coord, y_coord)
        if self.window_animation == "side-bounce":
            orbit_index = target_side_orbit_index(self.root, width, height, normalized, x_coord, y_coord)
            self.window_position = get_side_orbit_points(self.root, width, height)[orbit_index][0]
        else:
            self.window_position = normalized

    def apply_window_animation(self, animation: str, duration_ms: int) -> None:
        self.window_animation = validate_window_animation(animation)
        self.window_animation_duration_ms = max(int(duration_ms), 0)

    def on_window_click(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self.window_animation != "side-bounce":
            return
        self.window_animation_stopped_by_click = True
        self.cancel_window_animation()

    def cancel_window_animation(self) -> None:
        if self.window_animation_after_id is not None:
            try:
                self.root.after_cancel(self.window_animation_after_id)
            except ValueError:
                pass
            self.window_animation_after_id = None

    def animate_window_geometry(
        self,
        width: int,
        height: int,
        target_position: str,
        target_x: int,
        target_y: int,
    ) -> None:
        self.cancel_window_animation()
        target_geometry = f"{width}x{height}+{target_x}+{target_y}"
        if self.window_animation == "off" or self.window_animation_duration_ms <= 0:
            self.root.geometry(target_geometry)
            return

        current_geometry = self.root.winfo_geometry()
        try:
            _, _, current_x, current_y = parse_window_geometry(current_geometry)
        except ValueError:
            self.root.geometry(target_geometry)
            return

        def ease_out_cubic(progress: float) -> float:
            inv = 1.0 - progress
            return 1.0 - inv * inv * inv

        if self.window_animation == "side-bounce":
            orbit_points = build_side_bounce_waypoints(
                self.root, width, height, current_x, current_y, target_position, target_x, target_y
            )
            path = [(current_x, current_y)] + orbit_points
        else:
            orbit_points: list[tuple[int, int]] = []
            path = [(current_x, current_y), (target_x, target_y)]

        def run_path(points: list[tuple[int, int]], loop_orbit: bool = False) -> None:
            if len(points) < 2:
                self.root.geometry(target_geometry)
                self.window_animation_after_id = None
                return

            total_distance = 0
            segment_distances: list[int] = []
            for start_point, end_point in zip(points, points[1:]):
                segment_distance = max(
                    abs(end_point[0] - start_point[0]),
                    abs(end_point[1] - start_point[1]),
                )
                segment_distances.append(segment_distance)
                total_distance += segment_distance

            if total_distance == 0:
                self.root.geometry(f"{width}x{height}+{points[-1][0]}+{points[-1][1]}")
                self.window_animation_after_id = None
                return

            def animate_segment(segment_index: int) -> None:
                if segment_index >= len(points) - 1:
                    self.root.geometry(f"{width}x{height}+{points[-1][0]}+{points[-1][1]}")
                    if (
                        loop_orbit
                        and self.reminder_active
                        and not self.window_animation_stopped_by_click
                        and self.root.state() != "withdrawn"
                    ):
                        run_path([points[-1]] + orbit_points, loop_orbit=True)
                        return
                    self.window_animation_after_id = None
                    return

                start_point = points[segment_index]
                end_point = points[segment_index + 1]
                delta_x = end_point[0] - start_point[0]
                delta_y = end_point[1] - start_point[1]
                if delta_x == 0 and delta_y == 0:
                    animate_segment(segment_index + 1)
                    return

                segment_distance = segment_distances[segment_index]
                segment_duration_ms = max(
                    int(self.window_animation_duration_ms * (segment_distance / total_distance)),
                    80,
                )
                steps = max(4, min(20, segment_distance // 35 if segment_distance else 4))
                step_delay_ms = max(segment_duration_ms // steps, 10)

                def tick(step_index: int) -> None:
                    progress = step_index / steps
                    eased = ease_out_cubic(progress)
                    next_x = round(start_point[0] + (delta_x * eased))
                    next_y = round(start_point[1] + (delta_y * eased))
                    self.root.geometry(f"{width}x{height}+{next_x}+{next_y}")
                    if step_index >= steps:
                        animate_segment(segment_index + 1)
                        return
                    self.window_animation_after_id = self.root.after(
                        step_delay_ms, tick, step_index + 1
                    )

                tick(1)

            animate_segment(0)

        run_path(path, loop_orbit=self.window_animation == "side-bounce")

    def update_status_text(self, occupancy_state: str = "") -> None:
        trigger_modes: list[str] = []
        if self.current_interval_enabled:
            trigger_modes.append(f"every {self.current_interval}s")
        if self.current_presence_change_enabled and self.current_presence_entity:
            trigger_modes.append(f"on {self.current_presence_entity} change")
        mode_text = ", ".join(trigger_modes) if trigger_modes else "manual only"
        occupancy_text = ""
        effective_occupancy = occupancy_state.strip() or self.current_presence_value.strip()
        if self.current_presence_entity:
            display_value = effective_occupancy if effective_occupancy else "unknown"
            occupancy_text = f" Occupancy ({self.current_presence_entity}): {display_value}."
        self.status_var.set(f"Waiting for response (announce: {mode_text}).{occupancy_text}")

    def dismiss(self) -> None:
        if self.current_persist and self.reminder_active:
            if self.active_request_id:
                self.add_history("System", f"Request dismissed ({self.active_request_id})")
                self.dismissed_requests[self.active_request_id] = {
                    "ts": time.time(),
                    "cycle": self.current_cycle,
                }
                self.active_request_id = ""
            else:
                self.add_history("System", "Dismiss pressed; persistent reminder continues.")
            self.status_var.set("Persistent reminder active. Dismiss does not stop repeats.")
            self.root.withdraw()
            return

        if self.active_request_id:
            self.add_history("System", f"Request dismissed ({self.active_request_id})")
            self.dismissed_requests[self.active_request_id] = {
                "ts": time.time(),
                "cycle": self.current_cycle,
            }
            self.active_request_id = ""
        self.stop_active_reminder()
        self.reminder_active = False
        self.status_var.set("Reminder dismissed.")
        self.message_label.config(text="No active reminder.")
        self.subtext_label.config(text="")
        self.root.withdraw()

    def speak_once(self, text: str) -> None:
        cmd = ["python3", str(ELEVEN_SPEAK_SCRIPT), text]
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            print(f"11speak exited with code {result.returncode}")

    def add_history(self, sender: str, text: str) -> None:
        entry = {"ts": time.time(), "sender": sender, "text": text}
        with self.history_lock:
            self.history.append(entry)
            if len(self.history) > MAX_HISTORY_ITEMS:
                self.history = self.history[-MAX_HISTORY_ITEMS:]
            self.save_history()

    def load_history(self) -> None:
        try:
            if not self.history_path.exists():
                return
            raw = self.history_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                return
            clean: list[dict[str, Any]] = []
            for item in parsed[-MAX_HISTORY_ITEMS:]:
                if not isinstance(item, dict):
                    continue
                sender = str(item.get("sender") or "")
                text = str(item.get("text") or "")
                ts = float(item.get("ts") or time.time())
                if not sender:
                    continue
                clean.append({"ts": ts, "sender": sender, "text": text})
            self.history = clean
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.history = []

    def save_history(self) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.history_path.with_suffix(".tmp")
            payload = json.dumps(self.history[-MAX_HISTORY_ITEMS:], ensure_ascii=True, indent=2)
            tmp_path.write_text(payload + "\n", encoding="utf-8")
            tmp_path.replace(self.history_path)
        except OSError:
            pass

    def clear_history(self) -> None:
        with self.history_lock:
            self.history = []
            self.save_history()

    def generate_audio_cache(self, message: str, regenerate: bool = False) -> Path | None:
        message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        audio_path = AUDIO_CACHE_DIR / f"{message_hash}.mp3"
        if audio_path.exists() and audio_path.stat().st_size > 0 and not regenerate:
            return audio_path
        if audio_path.exists() and audio_path.stat().st_size == 0:
            audio_path.unlink()

        AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="custom_notify_build_", suffix=".mp3", dir=str(AUDIO_CACHE_DIR), delete=False
        ) as tmp:
            tmp_audio_path = Path(tmp.name)

        cmd = [
            "python3",
            str(ELEVEN_SPEAK_SCRIPT),
            "--no-speaker",
            "--save-stream",
            str(tmp_audio_path),
            message,
        ]
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0 or not tmp_audio_path.exists() or tmp_audio_path.stat().st_size == 0:
            if tmp_audio_path.exists():
                tmp_audio_path.unlink()
            print("Failed to cache narration audio; falling back to live generation.")
            return None
        tmp_audio_path.replace(audio_path)
        return audio_path

    def play_cached_audio(self, audio_path: Path) -> bool:
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(audio_path)]
        result = subprocess.run(cmd, check=False)
        return result.returncode == 0

    def send_response_from_ui(self) -> None:
        text = self.response_var.get().strip()
        if not text:
            self.status_var.set("Response is empty.")
            return
        if not self.active_request_id:
            self.status_var.set("No active request to respond to.")
            return
        self.add_history("Them", text)
        self.responses[self.active_request_id] = {"text": text, "ts": time.time()}
        self.active_request_id = ""
        threading.Thread(
            target=self.speak_once,
            args=("Confirmation: your message has been delivered. Daemon still active.",),
            daemon=True,
        ).start()
        self.status_var.set("Response sent.")
        self.response_var.set("")
        self.stop_active_reminder()
        self.message_label.config(text="No active reminder.")
        self.subtext_label.config(text="")
        self.root.withdraw()

    def speak_loop(
        self,
        message: str,
        interval_seconds: int,
        interval_enabled: bool,
        presence_entity: str,
        presence_change_enabled: bool,
        presence_poll_seconds: float,
        stop_event: threading.Event,
        audio_cache: Path | None,
        request_id: str,
    ) -> None:
        cycle = 0
        next_interval_at = time.monotonic() + max(interval_seconds, 1) if interval_enabled else None
        watch_presence = bool(presence_change_enabled and presence_entity.strip())
        next_presence_poll_at = time.monotonic() if watch_presence else None
        last_presence_state: str | None = None
        current_presence_state: str | None = None
        previous_presence_state: str | None = None

        if watch_presence:
            initial_state = get_entity_state_via_hass_cli(presence_entity, self.home_assistant_token)
            if initial_state is not None:
                last_presence_state = initial_state
                current_presence_state = initial_state
                self.current_presence_value = initial_state
                self.root.after(0, lambda: self.update_status_text(initial_state))

        def speak_now(trigger_reason: str) -> None:
            nonlocal cycle
            cycle += 1
            self.current_cycle = cycle
            occupancy_suffix = ""
            live_value = ""
            if watch_presence:
                live_value = (current_presence_state or last_presence_state or "").strip()
                if live_value:
                    occupancy_suffix = f" occupancy={live_value}"
            if request_id and request_id == self.active_request_id:
                self.add_history("System", f"Repeat spoke ({request_id}) cycle {cycle}{occupancy_suffix}")
                if trigger_reason != "interval":
                    self.add_history("System", f"Repeat trigger ({request_id}) cause: {trigger_reason}")
            if watch_presence:
                self.current_presence_value = live_value
                self.root.after(0, lambda: self.update_status_text(live_value))

            spoken_text = message
            if watch_presence and live_value:
                if spoken_text.strip():
                    if trigger_reason.startswith("presence change"):
                        spoken_text = f"{spoken_text}. Occupancy count is {live_value}."
                else:
                    spoken_text = f"Occupancy count is {live_value}."

            if spoken_text == message and audio_cache and audio_cache.exists():
                ok = self.play_cached_audio(audio_cache)
                if not ok:
                    print("Cached playback failed; falling back to live generation.")
                    result = subprocess.run(["python3", str(ELEVEN_SPEAK_SCRIPT), spoken_text], check=False)
                    if result.returncode != 0:
                        print(f"11speak exited with code {result.returncode}")
            else:
                result = subprocess.run(["python3", str(ELEVEN_SPEAK_SCRIPT), spoken_text], check=False)
                if result.returncode != 0:
                    print(f"11speak exited with code {result.returncode}")

        speak_now("initial")

        while not stop_event.is_set():
            now = time.monotonic()
            triggered = False
            trigger_reason = "interval"

            if watch_presence and next_presence_poll_at is not None and now >= next_presence_poll_at:
                polled_state = get_entity_state_via_hass_cli(presence_entity, self.home_assistant_token)
                next_presence_poll_at = now + max(presence_poll_seconds, 0.5)
                if polled_state is not None:
                    if last_presence_state is None:
                        last_presence_state = polled_state
                        current_presence_state = polled_state
                        self.current_presence_value = polled_state
                        self.root.after(0, lambda: self.update_status_text(polled_state))
                    elif polled_state != last_presence_state:
                        previous_presence_state = last_presence_state
                        current_presence_state = polled_state
                        last_presence_state = polled_state
                        self.current_presence_value = polled_state
                        self.root.after(0, lambda: self.update_status_text(polled_state))
                        triggered = True
                        trigger_reason = (
                            f"presence change {presence_entity}: "
                            f"{previous_presence_state} -> {current_presence_state}"
                        )

            if not triggered and next_interval_at is not None and now >= next_interval_at:
                triggered = True
                trigger_reason = "interval"
                next_interval_at = now + max(interval_seconds, 1)

            if triggered:
                speak_now(trigger_reason)
                if next_interval_at is not None:
                    next_interval_at = time.monotonic() + max(interval_seconds, 1)
                continue

            if stop_event.wait(0.2):
                break

    def stop_active_reminder(self) -> None:
        self.stop_event.set()
        self.cancel_window_animation()
        if self.speak_thread and self.speak_thread.is_alive():
            self.speak_thread.join(timeout=1)
        self.current_audio_file = None
        self.reminder_active = False
        self.current_subtext = ""
        self.current_persist = False
        self.current_interval_enabled = True
        self.current_presence_entity = ""
        self.current_presence_change_enabled = False
        self.current_presence_poll_seconds = DEFAULT_PRESENCE_POLL_SECONDS
        self.current_presence_value = ""
        self.current_cycle = 0

    def start_or_replace_reminder(
        self,
        message: str,
        interval_seconds: int,
        subtext: str = "",
        regenerate: bool = False,
        persist: bool = False,
        interval_enabled: bool = True,
        presence_entity: str = "",
        presence_change_enabled: bool = False,
        presence_poll_seconds: float = DEFAULT_PRESENCE_POLL_SECONDS,
    ) -> None:
        self.stop_active_reminder()

        self.current_message = message
        self.current_subtext = subtext
        self.current_persist = persist
        self.current_interval = interval_seconds
        self.current_interval_enabled = interval_enabled
        self.current_presence_entity = presence_entity.strip()
        self.current_presence_change_enabled = presence_change_enabled
        self.current_presence_poll_seconds = max(presence_poll_seconds, 0.5)
        self.current_presence_value = ""
        self.current_audio_file = self.generate_audio_cache(self.current_message, regenerate=regenerate)
        self.stop_event = threading.Event()
        self.speak_thread = threading.Thread(
            target=self.speak_loop,
            args=(
                self.current_message,
                self.current_interval,
                self.current_interval_enabled,
                self.current_presence_entity,
                self.current_presence_change_enabled,
                self.current_presence_poll_seconds,
                self.stop_event,
                self.current_audio_file,
                self.active_request_id,
            ),
            daemon=True,
        )
        self.speak_thread.start()
        self.reminder_active = True

        self.message_label.config(text=self.current_message)
        self.subtext_label.config(text=self.current_subtext)
        self.update_status_text()
        self.apply_window_position(self.window_position)
        self.root.deiconify()
        self.root.lift()

    def poll_commands(self) -> None:
        while True:
            try:
                payload = self.command_queue.get_nowait()
            except queue.Empty:
                break

            cmd = payload.get("cmd")
            if cmd == "trigger":
                request_id = str(payload.get("request_id") or "")
                if request_id and request_id != self.active_request_id:
                    continue
                presence_entity = str(payload.get("presence_entity") or "").strip()
                presence_change_enabled = bool(payload.get("presence_change_enabled", False))
                message = resolve_message_text(
                    str(payload.get("text") or ""),
                    presence_change_enabled,
                    presence_entity,
                )
                subtext = str(payload.get("subtext") or "")
                regenerate = bool(payload.get("regenerate"))
                persist = bool(payload.get("persist"))
                interval = int(payload.get("interval") or DEFAULT_INTERVAL_SECONDS)
                interval_enabled = bool(payload.get("interval_enabled", True))
                presence_poll_seconds = float(
                    payload.get("presence_poll_seconds") or DEFAULT_PRESENCE_POLL_SECONDS
                )
                if interval < 1:
                    interval = DEFAULT_INTERVAL_SECONDS
                self.start_or_replace_reminder(
                    message,
                    interval,
                    subtext,
                    regenerate=regenerate,
                    persist=persist,
                    interval_enabled=interval_enabled,
                    presence_entity=presence_entity,
                    presence_change_enabled=presence_change_enabled,
                    presence_poll_seconds=presence_poll_seconds,
                )
            elif cmd == "dismiss_active":
                if self.current_persist and self.reminder_active:
                    if self.active_request_id:
                        self.add_history("System", f"Request dismissed ({self.active_request_id})")
                        self.dismissed_requests[self.active_request_id] = {
                            "ts": time.time(),
                            "cycle": self.current_cycle,
                        }
                        self.active_request_id = ""
                    else:
                        self.add_history("System", "Dismiss command received; persistent reminder continues.")
                    self.status_var.set("Persistent reminder active. Dismiss does not stop repeats.")
                    self.root.withdraw()
                    continue
                if self.active_request_id:
                    self.add_history("System", f"Request dismissed ({self.active_request_id})")
                    self.dismissed_requests[self.active_request_id] = {
                        "ts": time.time(),
                        "cycle": self.current_cycle,
                    }
                    self.active_request_id = ""
                self.stop_active_reminder()
                self.message_label.config(text="No active reminder.")
                self.subtext_label.config(text="")
                self.root.withdraw()
            elif cmd == "complete_request":
                self.stop_active_reminder()
                self.message_label.config(text="No active reminder.")
                self.subtext_label.config(text="")
                self.root.withdraw()
            elif cmd == "set_subtext":
                subtext = str(payload.get("subtext") or "")
                if self.active_request_id:
                    self.add_history(
                        "System",
                        f"Subtext refreshed ({self.active_request_id}): {subtext if subtext else '[cleared]'}",
                    )
                    self.current_subtext = subtext
                    self.subtext_label.config(text=self.current_subtext)
            elif cmd == "set_window_position":
                self.apply_window_position(str(payload.get("window_position") or ""))
            elif cmd == "set_window_animation":
                self.apply_window_animation(
                    str(payload.get("window_animation") or DEFAULT_WINDOW_ANIMATION),
                    int(payload.get("window_animation_duration_ms") or DEFAULT_WINDOW_ANIMATION_DURATION_MS),
                )
            elif cmd == "shutdown":
                self.stop_active_reminder()
                self.shutdown_event.set()
                self.root.quit()

        if not self.shutdown_event.is_set():
            self.root.after(200, self.poll_commands)

    def handle_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        cmd = payload.get("cmd")

        if cmd == "ping":
            return {
                "ok": True,
                "status": "running",
                "pid": os.getpid(),
                "active": self.reminder_active,
                "message": self.current_message,
                "subtext": self.current_subtext,
                "interval": self.current_interval,
                "interval_enabled": self.current_interval_enabled,
                "presence_entity": self.current_presence_entity,
                "presence_change_enabled": self.current_presence_change_enabled,
                "presence_poll_seconds": self.current_presence_poll_seconds,
                "presence_value": self.current_presence_value,
                "persist": self.current_persist,
                "window_position": self.window_position,
                "window_animation": self.window_animation,
                "window_animation_duration_ms": self.window_animation_duration_ms,
                "active_request_id": self.active_request_id,
            }

        if cmd == "set_window_position":
            window_position = str(payload.get("window_position") or "")
            try:
                normalized = normalize_window_position(window_position)
                resolve_window_coordinates(self.root, normalized, 760, 280)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            self.command_queue.put({"cmd": "set_window_position", "window_position": normalized})
            return {"ok": True, "status": "window_position_queued", "window_position": normalized}

        if cmd == "set_window_animation":
            try:
                animation = validate_window_animation(str(payload.get("window_animation") or ""))
                duration_ms = max(
                    int(payload.get("window_animation_duration_ms") or DEFAULT_WINDOW_ANIMATION_DURATION_MS),
                    0,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            self.command_queue.put(
                {
                    "cmd": "set_window_animation",
                    "window_animation": animation,
                    "window_animation_duration_ms": duration_ms,
                }
            )
            return {
                "ok": True,
                "status": "window_animation_queued",
                "window_animation": animation,
                "window_animation_duration_ms": duration_ms,
            }

        if cmd == "trigger":
            request_id = str(uuid.uuid4())
            self.active_request_id = request_id
            self.dismissed_requests.pop(request_id, None)
            self.responses.pop(request_id, None)
            presence_entity = str(payload.get("presence_entity") or "")
            presence_change_enabled = bool(payload.get("presence_change_enabled", False))
            text = resolve_message_text(
                str(payload.get("text") or ""),
                presence_change_enabled,
                presence_entity,
            )
            subtext = str(payload.get("subtext") or "")
            history_text = text if text else "[occupancy count only]"
            self.add_history("You", history_text if not subtext else f"{history_text} [subtext: {subtext}]")
            self.command_queue.put(
                {
                    "cmd": "trigger",
                    "text": text,
                    "subtext": payload.get("subtext"),
                    "regenerate": payload.get("regenerate"),
                    "persist": payload.get("persist"),
                    "interval": payload.get("interval"),
                    "interval_enabled": payload.get("interval_enabled", True),
                    "presence_entity": payload.get("presence_entity"),
                    "presence_change_enabled": payload.get("presence_change_enabled", False),
                    "presence_poll_seconds": payload.get(
                        "presence_poll_seconds", DEFAULT_PRESENCE_POLL_SECONDS
                    ),
                    "request_id": request_id,
                }
            )
            return {"ok": True, "status": "triggered", "request_id": request_id}

        if cmd == "ask":
            request_id = str(uuid.uuid4())
            self.active_request_id = request_id
            self.responses.pop(request_id, None)
            self.dismissed_requests.pop(request_id, None)
            presence_entity = str(payload.get("presence_entity") or "")
            presence_change_enabled = bool(payload.get("presence_change_enabled", False))
            text = resolve_message_text(
                str(payload.get("text") or ""),
                presence_change_enabled,
                presence_entity,
            )
            subtext = str(payload.get("subtext") or "")
            history_text = text if text else "[occupancy count only]"
            self.add_history("You", history_text if not subtext else f"{history_text} [subtext: {subtext}]")
            self.command_queue.put(
                {
                    "cmd": "trigger",
                    "text": text,
                    "subtext": payload.get("subtext"),
                    "regenerate": payload.get("regenerate"),
                    "persist": payload.get("persist"),
                    "interval": payload.get("interval"),
                    "interval_enabled": payload.get("interval_enabled", True),
                    "presence_entity": payload.get("presence_entity"),
                    "presence_change_enabled": payload.get("presence_change_enabled", False),
                    "presence_poll_seconds": payload.get(
                        "presence_poll_seconds", DEFAULT_PRESENCE_POLL_SECONDS
                    ),
                    "request_id": request_id,
                }
            )
            return {"ok": True, "status": "awaiting_response", "request_id": request_id}

        if cmd == "respond":
            response_text = str(payload.get("text") or "").strip()
            if not response_text:
                return {"ok": False, "error": "response text is empty"}
            request_id = str(payload.get("request_id") or self.active_request_id or "")
            if not request_id:
                return {"ok": False, "error": "no active request"}
            self.add_history("Them", response_text)
            self.responses[request_id] = {"text": response_text, "ts": time.time()}
            if request_id == self.active_request_id:
                self.active_request_id = ""
                self.command_queue.put({"cmd": "complete_request"})
            return {"ok": True, "status": "response_recorded", "request_id": request_id}

        if cmd == "get_response":
            request_id = str(payload.get("request_id") or "").strip()
            if not request_id:
                return {"ok": False, "error": "request_id is required"}
            if request_id not in self.responses:
                if request_id in self.dismissed_requests:
                    info = self.dismissed_requests.pop(request_id)
                    return {
                        "ok": True,
                        "ready": True,
                        "dismissed": True,
                        "dismissed_ts": float(info.get("ts") or time.time()),
                        "dismissed_cycle": int(info.get("cycle") or 0),
                    }
                return {"ok": True, "ready": False}
            response_payload = self.responses.pop(request_id)
            response_text = str(response_payload.get("text") or "")
            response_ts = float(response_payload.get("ts") or time.time())
            return {"ok": True, "ready": True, "response": response_text, "response_ts": response_ts}

        if cmd == "get_history":
            limit = int(payload.get("limit") or 10)
            if limit < 1:
                limit = 1
            with self.history_lock:
                return {"ok": True, "history": self.history[-limit:]}

        if cmd == "set_subtext":
            self.command_queue.put({"cmd": "set_subtext", "subtext": payload.get("subtext")})
            return {"ok": True, "status": "subtext_queued"}

        if cmd == "clear_history":
            self.clear_history()
            return {"ok": True, "status": "history_cleared"}

        if cmd == "dismiss_active":
            self.command_queue.put({"cmd": "dismiss_active"})
            return {"ok": True, "status": "dismiss_queued"}

        if cmd == "shutdown":
            self.command_queue.put({"cmd": "shutdown"})
            return {"ok": True, "status": "shutting_down"}

        return {"ok": False, "error": f"unknown cmd: {cmd}"}

    def socket_server(self) -> None:
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket = server
        server.bind(str(SOCKET_PATH))
        server.listen(5)
        server.settimeout(1.0)

        while not self.shutdown_event.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            with conn:
                raw = conn.recv(65536)
                if not raw:
                    continue
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    response = {"ok": False, "error": "invalid json"}
                else:
                    response = self.handle_command(payload)
                try:
                    conn.sendall(json.dumps(response).encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError, OSError):
                    continue

        try:
            server.close()
        except OSError:
            pass

    def run(self) -> int:
        PID_PATH.write_text(f"{os.getpid()}\n", encoding="utf-8")

        server_thread = threading.Thread(target=self.socket_server, daemon=True)
        server_thread.start()

        self.root.after(200, self.poll_commands)
        self.root.mainloop()

        self.shutdown_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass

        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        if PID_PATH.exists():
            PID_PATH.unlink()
        return 0


def send_command(payload: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    response_raw = b""

    try:
        client.connect(str(SOCKET_PATH))
        client.sendall(json.dumps(payload).encode("utf-8"))
        response_raw = client.recv(65536)
    except TimeoutError:
        return {"ok": False, "error": "command timeout"}
    except OSError as exc:
        return {"ok": False, "error": f"socket error: {exc}"}
    finally:
        client.close()

    if not response_raw:
        return {"ok": False, "error": "empty response"}

    try:
        return json.loads(response_raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid response"}


def is_daemon_running() -> bool:
    if not SOCKET_PATH.exists():
        return False

    try:
        response = send_command({"cmd": "ping"}, timeout=0.7)
    except OSError:
        return False

    return bool(response.get("ok"))


def start_daemon(
    window_position: str = DEFAULT_WINDOW_POSITION,
    window_animation: str = DEFAULT_WINDOW_ANIMATION,
    window_animation_duration_ms: int = DEFAULT_WINDOW_ANIMATION_DURATION_MS,
) -> int:
    try:
        normalized = validate_window_position(window_position)
    except ValueError as exc:
        print(f"Invalid --window-position: {exc}")
        return 1
    try:
        animation = validate_window_animation(window_animation)
    except ValueError as exc:
        print(f"Invalid --window-animation: {exc}")
        return 1
    duration_ms = max(int(window_animation_duration_ms), 0)

    if is_daemon_running():
        if not set_live_window_animation(animation, duration_ms):
            return 1
        if set_live_window_position(normalized, required=normalized != DEFAULT_WINDOW_POSITION):
            print(
                f"Daemon already running. Window position set to {normalized} "
                f"with {animation} animation ({duration_ms}ms)."
            )
            return 0
        return 1

    this_script = Path(__file__).resolve()
    with open(os.devnull, "rb") as devnull_in, open(os.devnull, "ab") as devnull_out:
        subprocess.Popen(
            [
                "python3",
                str(this_script),
                "daemon",
                "--window-position",
                normalized,
                "--window-animation",
                animation,
                "--window-animation-duration-ms",
                str(duration_ms),
            ],
            stdin=devnull_in,
            stdout=devnull_out,
            stderr=devnull_out,
            start_new_session=True,
            close_fds=True,
        )

    print(
        f"Daemon start requested (window position: {normalized}, "
        f"animation: {animation}, duration: {duration_ms}ms)."
    )
    return 0


def stop_daemon() -> int:
    if not is_daemon_running():
        print("Daemon is not running.")
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        if PID_PATH.exists():
            PID_PATH.unlink()
        return 0

    response = send_command({"cmd": "shutdown"})
    if not response.get("ok"):
        print(f"Failed to stop daemon: {response}")
        return 1

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not is_daemon_running():
            print("Daemon stopped.")
            return 0
        time.sleep(0.2)

    print("Stop requested, but daemon is still running.")
    return 1


def status_daemon() -> int:
    if not is_daemon_running():
        print("Daemon status: stopped")
        return 1

    response = send_command({"cmd": "ping"})
    pid = response.get("pid", "unknown")
    active = bool(response.get("active"))
    request_id = str(response.get("active_request_id") or "")
    window_position = str(response.get("window_position") or DEFAULT_WINDOW_POSITION)
    window_animation = str(response.get("window_animation") or DEFAULT_WINDOW_ANIMATION)
    window_animation_duration_ms = int(
        response.get("window_animation_duration_ms") or DEFAULT_WINDOW_ANIMATION_DURATION_MS
    )
    if active:
        interval = response.get("interval", DEFAULT_INTERVAL_SECONDS)
        interval_enabled = bool(response.get("interval_enabled", True))
        presence_entity = str(response.get("presence_entity") or "")
        presence_change_enabled = bool(response.get("presence_change_enabled", False))
        presence_poll_seconds = float(
            response.get("presence_poll_seconds") or DEFAULT_PRESENCE_POLL_SECONDS
        )
        presence_value = str(response.get("presence_value") or "").strip()
        message = response.get("message", "")
        subtext = str(response.get("subtext") or "")
        persist = bool(response.get("persist"))
        mode_parts: list[str] = []
        if interval_enabled:
            mode_parts.append(f"every {interval}s")
        if presence_change_enabled and presence_entity:
            mode_parts.append(f"on {presence_entity} change (poll {presence_poll_seconds:.1f}s)")
        if not mode_parts:
            mode_parts.append("initial announcement only")
        print(f"Daemon status: running (pid {pid}) | active reminder {', '.join(mode_parts)}")
        if persist:
            print("Persistent mode: enabled (dismiss does not stop repeats)")
        if message:
            print(f"Current message: {message}")
        if presence_entity:
            print(f"Occupancy ({presence_entity}): {presence_value if presence_value else 'unknown'}")
        if subtext:
            print(f"Current subtext: {subtext}")
        if request_id:
            print(f"Awaiting response for request_id: {request_id}")
    else:
        print(f"Daemon status: running (pid {pid}) | idle")
    print(
        f"Window position: {window_position} | "
        f"animation: {window_animation} ({window_animation_duration_ms}ms)"
    )

    history = get_recent_history(limit=10)
    if history:
        print("Recent history:")
        for entry in history:
            sender = str(entry.get("sender") or "?")
            text = str(entry.get("text") or "")
            ts = float(entry.get("ts") or time.time())
            print(f"- {format_timestamp(ts)} {sender}: {text}")
    return 0


def trigger_reminder(
    text: str,
    interval: int,
    subtext: str = "",
    regenerate: bool = False,
    persist: bool = False,
    interval_enabled: bool = True,
    presence_entity: str = "",
    announce_on_presence_change: bool = False,
    presence_poll_seconds: float = DEFAULT_PRESENCE_POLL_SECONDS,
    window_position: str = DEFAULT_WINDOW_POSITION,
    window_animation: str = DEFAULT_WINDOW_ANIMATION,
    window_animation_duration_ms: int = DEFAULT_WINDOW_ANIMATION_DURATION_MS,
) -> int:
    if not is_daemon_running():
        script_path = Path(__file__).resolve()
        print("Daemon is not running. Start it first:")
        print(f"  python3 {script_path} start")
        return 1

    effective_presence_change = announce_on_presence_change and bool(presence_entity.strip())
    if announce_on_presence_change and not presence_entity.strip():
        print("Presence-change trigger requested but no --presence-entity set; ignoring that trigger.")

    if not set_live_window_animation(window_animation, window_animation_duration_ms):
        return 1
    if not set_live_window_position(window_position):
        return 1

    response = send_command(
        {
            "cmd": "trigger",
            "text": text,
            "subtext": subtext,
            "regenerate": regenerate,
            "persist": persist,
            "interval": interval,
            "interval_enabled": interval_enabled,
            "presence_entity": presence_entity,
            "presence_change_enabled": effective_presence_change,
            "presence_poll_seconds": presence_poll_seconds,
        }
    )
    if not response.get("ok"):
        print(f"Trigger failed: {response}")
        return 1

    request_id = str(response.get("request_id") or "")
    mode_parts: list[str] = []
    if interval_enabled:
        mode_parts.append(f"every {interval}s")
    if effective_presence_change and presence_entity.strip():
        mode_parts.append(
            f"on {presence_entity.strip()} change (poll {max(presence_poll_seconds, 0.5):.1f}s)"
        )
    if not mode_parts:
        mode_parts.append("initial announcement only")
    print(f"Reminder triggered ({', '.join(mode_parts)}).")
    if persist:
        print("Persistent mode enabled: dismiss will not stop repeats.")
    if subtext:
        print(f"Subtext: {subtext}")
    if request_id:
        print(f"Awaiting response (request_id={request_id}).")
    return 0


def get_recent_history(limit: int = 10) -> list[dict[str, Any]]:
    if not is_daemon_running():
        return []
    response = send_command({"cmd": "get_history", "limit": limit})
    if not response.get("ok"):
        return []
    history = response.get("history")
    if not isinstance(history, list):
        return []
    return [entry for entry in history if isinstance(entry, dict)]


def print_new_repeat_cycles(request_id: str, seen_cycles: set[int], limit: int = 50) -> None:
    prefix = f"Repeat spoke ({request_id}) cycle "
    history = get_recent_history(limit=limit)
    for entry in history:
        sender = str(entry.get("sender") or "")
        text = str(entry.get("text") or "")
        if sender != "System" or not text.startswith(prefix):
            continue
        cycle_payload = text[len(prefix) :].strip()
        cycle_token, _, remainder = cycle_payload.partition(" ")
        try:
            cycle = int(cycle_token)
        except (TypeError, ValueError):
            continue
        if cycle in seen_cycles:
            continue
        seen_cycles.add(cycle)
        ts = float(entry.get("ts") or time.time())
        if remainder:
            print(f"System {format_timestamp(ts)}: Repeat spoke cycle {cycle} ({remainder}).")
        else:
            print(f"System {format_timestamp(ts)}: Repeat spoke cycle {cycle}.")


def clear_history() -> int:
    if not is_daemon_running():
        print("Daemon is not running.")
        return 1
    response = send_command({"cmd": "clear_history"})
    if not response.get("ok"):
        print(f"Clear history failed: {response}")
        return 1
    print("History cleared.")
    return 0


def set_live_subtext(subtext: str) -> int:
    if not is_daemon_running():
        return 1
    response = send_command({"cmd": "set_subtext", "subtext": subtext})
    if not response.get("ok"):
        return 1
    return 0


def create_ask_request(
    text: str,
    interval: int,
    subtext: str = "",
    regenerate: bool = False,
    persist: bool = False,
    interval_enabled: bool = True,
    presence_entity: str = "",
    announce_on_presence_change: bool = False,
    presence_poll_seconds: float = DEFAULT_PRESENCE_POLL_SECONDS,
    window_position: str = DEFAULT_WINDOW_POSITION,
    window_animation: str = DEFAULT_WINDOW_ANIMATION,
    window_animation_duration_ms: int = DEFAULT_WINDOW_ANIMATION_DURATION_MS,
) -> str | None:
    effective_presence_change = announce_on_presence_change and bool(presence_entity.strip())
    if not set_live_window_animation(window_animation, window_animation_duration_ms):
        return None
    if not set_live_window_position(window_position):
        return None
    response = send_command(
        {
            "cmd": "ask",
            "text": text,
            "subtext": subtext,
            "regenerate": regenerate,
            "persist": persist,
            "interval": interval,
            "interval_enabled": interval_enabled,
            "presence_entity": presence_entity,
            "presence_change_enabled": effective_presence_change,
            "presence_poll_seconds": presence_poll_seconds,
        }
    )
    if not response.get("ok"):
        print(f"Ask failed: {response}")
        return None
    request_id = str(response.get("request_id") or "")
    if not request_id:
        print("Ask failed: missing request_id")
        return None
    return request_id


def wait_for_request_result(request_id: str, timeout: int) -> tuple[str, str, float]:
    start = time.time()
    while True:
        poll = send_command({"cmd": "get_response", "request_id": request_id}, timeout=2.0)
        if poll.get("ok") and poll.get("ready"):
            if poll.get("dismissed"):
                return ("dismissed", "", time.time())
            reply = str(poll.get("response") or "")
            reply_ts = float(poll.get("response_ts") or time.time())
            return ("response", reply, reply_ts)

        if timeout > 0 and (time.time() - start) >= timeout:
            return ("timeout", "", time.time())
        time.sleep(1.0)


def ask_and_wait_for_response(
    text: str,
    interval: int,
    timeout: int,
    subtext: str = "",
    regenerate: bool = False,
    quiet: bool = False,
    persist: bool = False,
    interval_enabled: bool = True,
    presence_entity: str = "",
    announce_on_presence_change: bool = False,
    presence_poll_seconds: float = DEFAULT_PRESENCE_POLL_SECONDS,
    window_position: str = DEFAULT_WINDOW_POSITION,
    window_animation: str = DEFAULT_WINDOW_ANIMATION,
    window_animation_duration_ms: int = DEFAULT_WINDOW_ANIMATION_DURATION_MS,
) -> int:
    if not is_daemon_running():
        script_path = Path(__file__).resolve()
        print("Daemon is not running. Start it first:")
        print(f"  python3 {script_path} start")
        return 1

    effective_presence_change = announce_on_presence_change and bool(presence_entity.strip())
    if announce_on_presence_change and not presence_entity.strip() and not quiet:
        print("Presence-change trigger requested but no --presence-entity set; ignoring that trigger.")

    request_id = create_ask_request(
        text,
        interval,
        subtext,
        regenerate=regenerate,
        persist=persist,
        interval_enabled=interval_enabled,
        presence_entity=presence_entity,
        announce_on_presence_change=effective_presence_change,
        presence_poll_seconds=presence_poll_seconds,
        window_position=window_position,
        window_animation=window_animation,
        window_animation_duration_ms=window_animation_duration_ms,
    )
    if not request_id:
        return 1

    if subtext and not quiet:
        print(f"Subtext: {subtext}")
    if persist and not quiet:
        print("Persistent mode enabled: dismiss will not stop repeats.")
    if not quiet:
        print(f"Awaiting response (request_id={request_id})...")
    start = time.time()
    seen_repeat_cycles: set[int] = set()
    while True:
        status, reply, reply_ts = wait_for_request_result(request_id, 1)
        if status == "response":
            if not quiet:
                print_new_repeat_cycles(request_id, seen_repeat_cycles)
            print(f"Response {format_timestamp(reply_ts)}: {reply}")
            return 0
        if status == "dismissed":
            if not quiet:
                print_new_repeat_cycles(request_id, seen_repeat_cycles)
            print("Request dismissed.")
            return 0
        if status == "timeout":
            if not quiet:
                print_new_repeat_cycles(request_id, seen_repeat_cycles)
            if timeout > 0 and (time.time() - start) >= timeout:
                print("Timed out waiting for response.")
                return 1
            continue
        print("Unexpected wait status.")
        return 1


def respond_to_active_request(text: str, request_id: str) -> int:
    if not is_daemon_running():
        print("Daemon is not running.")
        return 1
    payload: dict[str, Any] = {"cmd": "respond", "text": text}
    if request_id.strip():
        payload["request_id"] = request_id.strip()
    response = send_command(payload)
    if not response.get("ok"):
        print(f"Respond failed: {response}")
        return 1
    print(f"Response recorded (request_id={response.get('request_id')}).")
    return 0


def dismiss_active_request() -> int:
    if not is_daemon_running():
        print("Daemon is not running.")
        return 1
    pre = send_command({"cmd": "ping"})
    target_request_id = str(pre.get("active_request_id") or "")
    was_persist = bool(pre.get("persist"))

    response = send_command({"cmd": "dismiss_active"})
    if not response.get("ok"):
        print(f"Dismiss failed: {response}")
        return 1

    deadline = time.time() + 3.0
    while time.time() < deadline:
        state = send_command({"cmd": "ping"}, timeout=1.0)
        if not state.get("ok"):
            break
        current_request_id = str(state.get("active_request_id") or "")
        active = bool(state.get("active"))
        if target_request_id:
            if current_request_id != target_request_id:
                break
        else:
            if not current_request_id and not active:
                break
        time.sleep(0.1)

    if was_persist:
        print("Request dismissed; persistent reminder continues.")
    else:
        print("Active reminder dismissed.")
    return 0


def ensure_daemon_running(
    window_position: str = DEFAULT_WINDOW_POSITION,
    window_animation: str = DEFAULT_WINDOW_ANIMATION,
    window_animation_duration_ms: int = DEFAULT_WINDOW_ANIMATION_DURATION_MS,
) -> bool:
    if is_daemon_running():
        if not set_live_window_animation(window_animation, window_animation_duration_ms):
            return False
        return set_live_window_position(window_position)
    start_daemon(window_position, window_animation, window_animation_duration_ms)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if is_daemon_running():
            return True
        time.sleep(0.2)
    return False


def interactive_chat_loop(
    interval: int,
    quiet: bool = False,
    persist: bool = False,
    interval_enabled: bool = True,
    speak_dialogue: bool = False,
    presence_entity: str = "",
    announce_on_presence_change: bool = False,
    presence_poll_seconds: float = DEFAULT_PRESENCE_POLL_SECONDS,
    window_position: str = DEFAULT_WINDOW_POSITION,
    window_animation: str = DEFAULT_WINDOW_ANIMATION,
    window_animation_duration_ms: int = DEFAULT_WINDOW_ANIMATION_DURATION_MS,
) -> int:
    daemon_was_running = is_daemon_running()
    if not ensure_daemon_running(window_position, window_animation, window_animation_duration_ms):
        print("Unable to start daemon.")
        return 1

    def info(message: str) -> None:
        if not quiet:
            print(message)

    info("Interactive mode started. Type messages to send.")
    info(
        "Commands: /dismiss detach, /quit stop daemon, /subtext <text>, /regenerate on|off, "
        "/persist on|off, /interval-mode on|off, /speak-dialogue on|off, /presence-entity <entity_id>, "
        "/presence-change on|off, /presence-poll <seconds>, /clear-history."
    )
    if daemon_was_running and not quiet:
        history = get_recent_history(limit=10)
        if history:
            print("Recent history:")
            for entry in history:
                sender = str(entry.get("sender") or "?")
                text = str(entry.get("text") or "")
                ts = float(entry.get("ts") or time.time())
                print(f"- {format_timestamp(ts)} {sender}: {text}")

    current_subtext = ""
    regenerate_audio = False
    persist_mode = persist
    interval_enabled_mode = interval_enabled
    speak_dialogue_mode = speak_dialogue
    presence_entity_mode = presence_entity.strip()
    announce_on_presence_change_mode = announce_on_presence_change
    presence_poll_seconds_mode = max(presence_poll_seconds, 0.5)
    inline_subtext_marker = " /subtext "

    def handle_chat_control(command: str) -> str:
        nonlocal current_subtext
        nonlocal regenerate_audio
        nonlocal persist_mode
        nonlocal interval_enabled_mode
        nonlocal speak_dialogue_mode
        nonlocal presence_entity_mode
        nonlocal announce_on_presence_change_mode
        nonlocal presence_poll_seconds_mode

        if command.startswith("/subtext"):
            parts = command.split(" ", 1)
            current_subtext = parts[1].strip() if len(parts) > 1 else ""
            set_live_subtext(current_subtext)
            if current_subtext:
                info(f"Subtext set: {current_subtext}")
            else:
                info("Subtext cleared.")
            return "handled"

        if command.startswith("/persist"):
            parts = command.split(" ", 1)
            value = parts[1].strip().lower() if len(parts) > 1 else ""
            if value in {"on", "true", "1"}:
                persist_mode = True
                info("Persist enabled.")
            elif value in {"off", "false", "0"}:
                persist_mode = False
                info("Persist disabled.")
            elif value == "":
                persist_mode = not persist_mode
                info(f"Persist {'enabled' if persist_mode else 'disabled'}.")
            else:
                print("Usage: /persist on|off")
            return "handled"

        if command.startswith("/regenerate") or command.startswith("/regeneration"):
            parts = command.split(" ", 1)
            value = parts[1].strip().lower() if len(parts) > 1 else ""
            if value in {"on", "true", "1"}:
                regenerate_audio = True
                info("Regenerate enabled.")
            elif value in {"off", "false", "0"}:
                regenerate_audio = False
                info("Regenerate disabled.")
            elif value == "":
                regenerate_audio = not regenerate_audio
                info(f"Regenerate {'enabled' if regenerate_audio else 'disabled'}.")
            else:
                print("Usage: /regenerate on|off")
            return "handled"

        if command.startswith("/interval-mode"):
            parts = command.split(" ", 1)
            value = parts[1].strip().lower() if len(parts) > 1 else ""
            if value in {"on", "true", "1"}:
                interval_enabled_mode = True
                info("Interval trigger enabled.")
            elif value in {"off", "false", "0"}:
                interval_enabled_mode = False
                info("Interval trigger disabled.")
            elif value == "":
                interval_enabled_mode = not interval_enabled_mode
                info(f"Interval trigger {'enabled' if interval_enabled_mode else 'disabled'}.")
            else:
                print("Usage: /interval-mode on|off")
            return "handled"

        if command.startswith("/speak-dialogue"):
            parts = command.split(" ", 1)
            value = parts[1].strip().lower() if len(parts) > 1 else ""
            if value in {"on", "true", "1"}:
                speak_dialogue_mode = True
                info("Dialogue speech enabled.")
            elif value in {"off", "false", "0"}:
                speak_dialogue_mode = False
                info("Dialogue speech disabled.")
            elif value == "":
                speak_dialogue_mode = not speak_dialogue_mode
                info(f"Dialogue speech {'enabled' if speak_dialogue_mode else 'disabled'}.")
            else:
                print("Usage: /speak-dialogue on|off")
            return "handled"

        if command.startswith("/presence-entity"):
            parts = command.split(" ", 1)
            presence_entity_mode = parts[1].strip() if len(parts) > 1 else ""
            if presence_entity_mode:
                info(f"Presence entity set: {presence_entity_mode}")
            else:
                info("Presence entity cleared.")
            return "handled"

        if command.startswith("/presence-change"):
            parts = command.split(" ", 1)
            value = parts[1].strip().lower() if len(parts) > 1 else ""
            if value in {"on", "true", "1"}:
                announce_on_presence_change_mode = True
                info("Presence-change trigger enabled.")
            elif value in {"off", "false", "0"}:
                announce_on_presence_change_mode = False
                info("Presence-change trigger disabled.")
            elif value == "":
                announce_on_presence_change_mode = not announce_on_presence_change_mode
                info(
                    "Presence-change trigger "
                    f"{'enabled' if announce_on_presence_change_mode else 'disabled'}."
                )
            else:
                print("Usage: /presence-change on|off")
            return "handled"

        if command.startswith("/presence-poll"):
            parts = command.split(" ", 1)
            value = parts[1].strip() if len(parts) > 1 else ""
            if not value:
                print("Usage: /presence-poll <seconds>")
                return "handled"
            try:
                poll = float(value)
            except ValueError:
                print("Usage: /presence-poll <seconds>")
                return "handled"
            presence_poll_seconds_mode = max(poll, 0.5)
            info(f"Presence poll set to {presence_poll_seconds_mode:.1f}s.")
            return "handled"

        if command == "/clear-history":
            clear_history()
            return "handled"

        if command == "/dismiss":
            info("Interactive session detached.")
            return "detach"

        if command in {"/quit", "/exit"}:
            stop_daemon()
            info("Interactive session ended.")
            return "quit"

        return "none"

    def parse_outgoing_with_inline(raw: str) -> tuple[str, str] | None:
        effective_outgoing = raw
        effective_subtext = current_subtext
        if inline_subtext_marker in raw:
            message_part, subtext_part = raw.split(inline_subtext_marker, 1)
            message_part = message_part.strip()
            subtext_part = subtext_part.strip()
            if not message_part:
                print("Message text is required before /subtext in inline form.")
                return None
            if not subtext_part:
                print("Inline /subtext requires text after it.")
                return None
            effective_outgoing = message_part
            effective_subtext = subtext_part
            info(f"Inline subtext: {effective_subtext}")
        return (effective_outgoing, effective_subtext)

    while True:
        try:
            outgoing = input("You> ").strip()
        except EOFError:
            print()
            stop_daemon()
            info("Interactive session ended.")
            return 0
        except KeyboardInterrupt:
            print()
            stop_daemon()
            info("Interactive session ended.")
            return 0

        if not outgoing:
            continue
        action = handle_chat_control(outgoing)
        if action == "handled":
            continue
        if action == "detach":
            return 0
        if action == "quit":
            return 0

        parsed = parse_outgoing_with_inline(outgoing)
        if not parsed:
            continue
        effective_outgoing, effective_subtext = parsed

        request_id = create_ask_request(
            effective_outgoing,
            interval,
            effective_subtext,
            regenerate=regenerate_audio,
            persist=persist_mode,
            interval_enabled=interval_enabled_mode,
            presence_entity=presence_entity_mode,
            announce_on_presence_change=announce_on_presence_change_mode,
            presence_poll_seconds=presence_poll_seconds_mode,
            window_position=window_position,
            window_animation=window_animation,
            window_animation_duration_ms=window_animation_duration_ms,
        )
        if not request_id:
            return 1
        info("Waiting for response... (type /dismiss, /quit, or send another message)")
        seen_repeat_cycles: set[int] = set()
        while True:
            try:
                status, reply, reply_ts = wait_for_request_result(request_id, 1)
            except KeyboardInterrupt:
                print()
                stop_daemon()
                info("Interactive session ended.")
                return 0

            if status == "response":
                if not quiet:
                    print_new_repeat_cycles(request_id, seen_repeat_cycles)
                print(f"Them {format_timestamp(reply_ts)}: {reply}")
                if speak_dialogue_mode:
                    threading.Thread(target=speak_text_once_local, args=(reply,), daemon=True).start()
                break
            if status == "dismissed":
                if not quiet:
                    print_new_repeat_cycles(request_id, seen_repeat_cycles)
                print("Conversation dismissed by the other party.")
                if speak_dialogue_mode:
                    threading.Thread(
                        target=speak_text_once_local,
                        args=("Conversation dismissed by the other party.",),
                        daemon=True,
                    ).start()
                break
            if status == "timeout":
                if not quiet:
                    print_new_repeat_cycles(request_id, seen_repeat_cycles)
            else:
                print("Unexpected wait status.")
                return 1

            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0)
            except (OSError, ValueError):
                ready = []

            if not ready:
                continue

            command = sys.stdin.readline()
            if command == "":
                stop_daemon()
                info("Interactive session ended.")
                return 0
            command = command.strip()
            if not command:
                continue
            action = handle_chat_control(command)
            if action == "handled":
                continue
            if action == "detach":
                return 0
            if action == "quit":
                return 0
            parsed = parse_outgoing_with_inline(command)
            if not parsed:
                continue
            next_outgoing, next_subtext = parsed
            next_request_id = create_ask_request(
                next_outgoing,
                interval,
                next_subtext,
                regenerate=regenerate_audio,
                persist=persist_mode,
                interval_enabled=interval_enabled_mode,
                presence_entity=presence_entity_mode,
                announce_on_presence_change=announce_on_presence_change_mode,
                presence_poll_seconds=presence_poll_seconds_mode,
                window_position=window_position,
                window_animation=window_animation,
                window_animation_duration_ms=window_animation_duration_ms,
            )
            if not next_request_id:
                print("Failed to send additional message; still waiting on prior request.")
                continue
            request_id = next_request_id
            seen_repeat_cycles = set()
            info(f"Sent new message. Now waiting (request_id={request_id})...")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Daemonized GUI reminder with terminal/GUI two-way messaging."
    )
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser("start", help="Start daemon in background")
    start.add_argument(
        "--window-position",
        default=DEFAULT_WINDOW_POSITION,
        help=window_position_help_text(),
    )
    start.add_argument(
        "--window-animation",
        default=DEFAULT_WINDOW_ANIMATION,
        help="Window motion style: smooth, side-bounce, or off",
    )
    start.add_argument(
        "--window-animation-duration-ms",
        type=int,
        default=DEFAULT_WINDOW_ANIMATION_DURATION_MS,
        help="Window motion duration in milliseconds (default: 220)",
    )
    subparsers.add_parser("stop", help="Stop daemon")
    subparsers.add_parser("status", help="Show daemon status")
    subparsers.add_parser("dismiss", help="Dismiss active reminder without stopping daemon")
    subparsers.add_parser("clear-history", help="Clear persisted and in-memory history")
    daemon = subparsers.add_parser("daemon", help=argparse.SUPPRESS)
    daemon.add_argument(
        "--window-position",
        default=DEFAULT_WINDOW_POSITION,
        help=argparse.SUPPRESS,
    )
    daemon.add_argument(
        "--window-animation",
        default=DEFAULT_WINDOW_ANIMATION,
        help=argparse.SUPPRESS,
    )
    daemon.add_argument(
        "--window-animation-duration-ms",
        type=int,
        default=DEFAULT_WINDOW_ANIMATION_DURATION_MS,
        help=argparse.SUPPRESS,
    )

    chat = subparsers.add_parser(
        "chat",
        help="Interactive terminal mode (default when no subcommand is provided)",
    )
    chat.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Seconds between reminder repeats while awaiting response (only used when explicitly provided)",
    )
    chat.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce chat terminal output (hide wait/status/repeat logs)",
    )
    chat.add_argument(
        "--persist",
        action="store_true",
        help="Keep repeating even if dismissed",
    )
    chat.add_argument(
        "--no-interval",
        action="store_true",
        help="Disable interval repeats; only initial and/or presence-change announcements run",
    )
    chat.add_argument(
        "--speak-dialogue",
        action="store_true",
        help="Speak incoming dialogue updates aloud in chat mode",
    )
    chat.add_argument(
        "--presence-entity",
        default="",
        help="Home Assistant entity_id to watch for state changes (for example sensor.apollo_mtr_1_12022c_presence_target_count)",
    )
    chat.add_argument(
        "--announce-on-presence-change",
        action="store_true",
        help="Announce whenever --presence-entity changes state",
    )
    chat.add_argument(
        "--presence-poll-seconds",
        type=float,
        default=DEFAULT_PRESENCE_POLL_SECONDS,
        help="Polling interval for presence entity state changes (default: 2.0)",
    )
    chat.add_argument(
        "--window-position",
        default=DEFAULT_WINDOW_POSITION,
        help=window_position_help_text(),
    )
    chat.add_argument(
        "--window-animation",
        default=DEFAULT_WINDOW_ANIMATION,
        help="Window motion style: smooth, side-bounce, or off",
    )
    chat.add_argument(
        "--window-animation-duration-ms",
        type=int,
        default=DEFAULT_WINDOW_ANIMATION_DURATION_MS,
        help="Window motion duration in milliseconds (default: 220)",
    )

    trigger = subparsers.add_parser("trigger", help="Start reminder loop with custom text")
    trigger.add_argument("text", nargs="?", default="", help="Text to speak (optional in presence-count mode)")
    trigger.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Seconds between repeats (only used when explicitly provided)",
    )
    trigger.add_argument(
        "--subtext",
        default="",
        help="Optional non-spoken subtitle shown in GUI",
    )
    trigger.add_argument(
        "--regenerate",
        action="store_true",
        help="Force regeneration of cached speech audio for this message",
    )
    trigger.add_argument(
        "--persist",
        action="store_true",
        help="Keep repeating even if dismissed",
    )
    trigger.add_argument(
        "--no-interval",
        action="store_true",
        help="Disable interval repeats; only initial and/or presence-change announcements run",
    )
    trigger.add_argument(
        "--presence-entity",
        default="",
        help="Home Assistant entity_id to watch for state changes (for example sensor.apollo_mtr_1_12022c_presence_target_count)",
    )
    trigger.add_argument(
        "--announce-on-presence-change",
        action="store_true",
        help="Announce whenever --presence-entity changes state",
    )
    trigger.add_argument(
        "--presence-poll-seconds",
        type=float,
        default=DEFAULT_PRESENCE_POLL_SECONDS,
        help="Polling interval for presence entity state changes (default: 2.0)",
    )
    trigger.add_argument(
        "--window-position",
        default=DEFAULT_WINDOW_POSITION,
        help=window_position_help_text(),
    )
    trigger.add_argument(
        "--window-animation",
        default=DEFAULT_WINDOW_ANIMATION,
        help="Window motion style: smooth, side-bounce, or off",
    )
    trigger.add_argument(
        "--window-animation-duration-ms",
        type=int,
        default=DEFAULT_WINDOW_ANIMATION_DURATION_MS,
        help="Window motion duration in milliseconds (default: 220)",
    )

    ask = subparsers.add_parser(
        "ask",
        help="Trigger reminder and wait for response from GUI or respond command",
    )
    ask.add_argument("text", nargs="?", default="", help="Text to speak (optional in presence-count mode)")
    ask.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Seconds between repeats while waiting (only used when explicitly provided)",
    )
    ask.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Seconds to wait for response, 0 waits indefinitely (default: 0)",
    )
    ask.add_argument(
        "--subtext",
        default="",
        help="Optional non-spoken subtitle shown in GUI",
    )
    ask.add_argument(
        "--regenerate",
        action="store_true",
        help="Force regeneration of cached speech audio for this message",
    )
    ask.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce ask terminal output (hide wait/status/repeat logs)",
    )
    ask.add_argument(
        "--persist",
        action="store_true",
        help="Keep repeating even if dismissed",
    )
    ask.add_argument(
        "--no-interval",
        action="store_true",
        help="Disable interval repeats; only initial and/or presence-change announcements run",
    )
    ask.add_argument(
        "--presence-entity",
        default="",
        help="Home Assistant entity_id to watch for state changes (for example sensor.apollo_mtr_1_12022c_presence_target_count)",
    )
    ask.add_argument(
        "--announce-on-presence-change",
        action="store_true",
        help="Announce whenever --presence-entity changes state",
    )
    ask.add_argument(
        "--presence-poll-seconds",
        type=float,
        default=DEFAULT_PRESENCE_POLL_SECONDS,
        help="Polling interval for presence entity state changes (default: 2.0)",
    )
    ask.add_argument(
        "--window-position",
        default=DEFAULT_WINDOW_POSITION,
        help=window_position_help_text(),
    )
    ask.add_argument(
        "--window-animation",
        default=DEFAULT_WINDOW_ANIMATION,
        help="Window motion style: smooth, side-bounce, or off",
    )
    ask.add_argument(
        "--window-animation-duration-ms",
        type=int,
        default=DEFAULT_WINDOW_ANIMATION_DURATION_MS,
        help="Window motion duration in milliseconds (default: 220)",
    )

    respond = subparsers.add_parser(
        "respond",
        help="Post a response back to the current ask request (or request id)",
    )
    respond.add_argument("text", help="Response text")
    respond.add_argument("--request-id", default="", help="Optional request id")

    args = parser.parse_args()
    if not args.command:
        args.command = "chat"
        args.interval = DEFAULT_INTERVAL_SECONDS
        args.quiet = False
        args.persist = False
        args.no_interval = False
        args.speak_dialogue = False
        args.presence_entity = ""
        args.announce_on_presence_change = False
        args.presence_poll_seconds = DEFAULT_PRESENCE_POLL_SECONDS
        args.window_position = DEFAULT_WINDOW_POSITION
        args.window_animation = DEFAULT_WINDOW_ANIMATION
        args.window_animation_duration_ms = DEFAULT_WINDOW_ANIMATION_DURATION_MS
    return args


def main() -> int:
    if not ELEVEN_SPEAK_SCRIPT.exists():
        print(f"Missing 11speak script at {ELEVEN_SPEAK_SCRIPT}")
        return 1

    if "ELEVENLABS_API_KEY" not in os.environ:
        print("ELEVENLABS_API_KEY is not set. 11speak calls will fail until it is set.")

    args = parse_args()
    cli_argv = sys.argv[1:]

    if args.command == "start":
        return start_daemon(
            args.window_position,
            args.window_animation,
            args.window_animation_duration_ms,
        )
    if args.command == "stop":
        return stop_daemon()
    if args.command == "status":
        return status_daemon()
    if args.command == "dismiss":
        return dismiss_active_request()
    if args.command == "clear-history":
        return clear_history()
    if args.command == "chat":
        interval = args.interval if args.interval >= 1 else DEFAULT_INTERVAL_SECONDS
        interval_enabled = (not args.no_interval) and option_was_provided("--interval", cli_argv)
        if (
            args.announce_on_presence_change
            and args.presence_entity.strip()
            and not args.no_interval
            and not option_was_provided("--interval", cli_argv)
        ):
            interval_enabled = False
        return interactive_chat_loop(
            interval,
            quiet=args.quiet,
            persist=args.persist,
            interval_enabled=interval_enabled,
            speak_dialogue=args.speak_dialogue,
            presence_entity=args.presence_entity,
            announce_on_presence_change=args.announce_on_presence_change,
            presence_poll_seconds=args.presence_poll_seconds,
            window_position=args.window_position,
            window_animation=args.window_animation,
            window_animation_duration_ms=args.window_animation_duration_ms,
        )
    if args.command == "trigger":
        interval = args.interval if args.interval >= 1 else DEFAULT_INTERVAL_SECONDS
        interval_enabled = (not args.no_interval) and option_was_provided("--interval", cli_argv)
        if (
            args.announce_on_presence_change
            and args.presence_entity.strip()
            and not args.no_interval
            and not option_was_provided("--interval", cli_argv)
        ):
            interval_enabled = False
        return trigger_reminder(
            args.text,
            interval,
            args.subtext,
            regenerate=args.regenerate,
            persist=args.persist,
            interval_enabled=interval_enabled,
            presence_entity=args.presence_entity,
            announce_on_presence_change=args.announce_on_presence_change,
            presence_poll_seconds=args.presence_poll_seconds,
            window_position=args.window_position,
            window_animation=args.window_animation,
            window_animation_duration_ms=args.window_animation_duration_ms,
        )
    if args.command == "ask":
        interval = args.interval if args.interval >= 1 else DEFAULT_INTERVAL_SECONDS
        interval_enabled = (not args.no_interval) and option_was_provided("--interval", cli_argv)
        if (
            args.announce_on_presence_change
            and args.presence_entity.strip()
            and not args.no_interval
            and not option_was_provided("--interval", cli_argv)
        ):
            interval_enabled = False
        timeout = args.timeout if args.timeout >= 0 else 0
        return ask_and_wait_for_response(
            args.text,
            interval,
            timeout,
            args.subtext,
            regenerate=args.regenerate,
            quiet=args.quiet,
            persist=args.persist,
            interval_enabled=interval_enabled,
            presence_entity=args.presence_entity,
            announce_on_presence_change=args.announce_on_presence_change,
            presence_poll_seconds=args.presence_poll_seconds,
            window_position=args.window_position,
            window_animation=args.window_animation,
            window_animation_duration_ms=args.window_animation_duration_ms,
        )
    if args.command == "respond":
        return respond_to_active_request(args.text, args.request_id)
    if args.command == "daemon":
        daemon = NotifierDaemon()
        daemon.apply_window_animation(args.window_animation, args.window_animation_duration_ms)
        daemon.apply_window_position(args.window_position)
        return daemon.run()

    print(f"Unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
