#!/usr/bin/env python3
"""
MasterBot (revamped) — reintroducing the classic features, but updated for discord.py 2.x.

Features brought back / modernized:
- Per-server XP/Level system w/ achievements + "new rules" + TRANSMIGRATION/DELETION arc
- Dice roller (+roll ...) with sane limits + safer math handling
- RPG stats storage (+setstats ...) including AC/HP calc, and HP adjustments (+hp ...)
- Stats display (+stats, +allstats)
- The old "+masterbot" monologue gag
- Optional Star Wars ASCII animation (+starwars) if ./bin/sw1.txt exists
- Private messaging support:
  - +dm @user <message> (send a DM)
  - DMs to the bot get a small auto-response and commands work in DMs too

Run:
  export DISCORD_TOKEN="YOUR_TOKEN"
  python3 masterbot_revamped.py

Notes:
- Uses a shelve DB at ./logs/masterbot.db (auto-created).
- Channel “routing” is preserved via optional env vars:
    MASTERBOT_LEADERBOARD_<GUILD_ID>=<CHANNEL_ID>
    MASTERBOT_DICEBOARD_<GUILD_ID>=<CHANNEL_ID>
    MASTERBOT_WELCOME_<GUILD_ID>=<CHANNEL_ID>
  If not set, it posts to the channel where the trigger happened.
"""

from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict
import concurrent.futures
import contextlib
import datetime
import difflib
import hashlib
import heapq
import json
import logging
import math
import os
import pickle
import random
import re
import shelve
import shutil
import subprocess
import sys
import time
import string
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands, tasks
from openai import OpenAI

# ----------------------------
# Logging
# ----------------------------
LOG_DIR = Path("./logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

log_formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("masterbot")
logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler(LOG_DIR / "masterbot.log")
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.DEBUG)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.DEBUG)
logger.addHandler(console_handler)

# ----------------------------
# Storage
# ----------------------------
DB_PATH = str(LOG_DIR / "masterbot.db")
SOL_EMBED_CACHE_PATH = LOG_DIR / "sol_embeddings.pkl"
SOL_HISTORY_LIMIT = 20

DEFAULT_USER = {
    "level": 0,
    "xp": 1,
    "achievements": [],
    "inspiration": 0,
    "dicerolls": 1,
    "wordcount": 0,
    "words": {},
    # RPG extras (optional)
    # "strength": [score, mod], ...
    # "ac": int
    # "hp": int
}

DEFAULT_SERVER = {
    "users": {},
    "newrules": False,
}


def _new_default_user() -> Dict[str, Any]:
    # Build fresh nested containers to avoid shared mutable defaults across users.
    return {
        "level": 0,
        "xp": 1,
        "achievements": [],
        "inspiration": 0,
        "dicerolls": 1,
        "wordcount": 0,
        "words": {},
    }


def _new_default_server() -> Dict[str, Any]:
    # Build fresh nested containers to avoid shared mutable defaults across guilds.
    return {
        "users": {},
        "newrules": False,
    }

# ----------------------------
# Discord setup
# ----------------------------
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
if not TOKEN:
    logger.warning("DISCORD_TOKEN is not set. Export it before running.")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # for mentions/user objects; safe default

bot = commands.Bot(command_prefix="+", intents=intents, help_command=None)
openai_client = OpenAI()

VOICE_LOOP_PRESET_CHANNEL_IDS_DEFAULT = [
    496375061134049298,   # Electronics / walkie
    631005215222661134,   # test / General
    1072011626573729896,  # Deer Valley / General
    1476758309586468915,  # D&D / piezo
]
VOICE_LOOP_PRESET_CHANNEL_IDS = list(VOICE_LOOP_PRESET_CHANNEL_IDS_DEFAULT)
VOICE_LOOP_MEDIA_DIR = Path("/home/david/random")
VOICE_LOOP_BOOT_INTRO_FILES: List[Path] = [
    VOICE_LOOP_MEDIA_DIR / "masterbot_runtime_unified_boot_intro.mp3",
    VOICE_LOOP_MEDIA_DIR / "masterbot_voice_handshake_bugfix_short_screenplay_log.mp3",
]
VOICE_CONNECT_RETRIES = 4
VOICE_CONNECT_RETRY_BASE_S = 1.5
VOICE_RECOVER_SLEEP_S = 3.0
VOICE_LOOP_PLAYABLE_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".flac",
    ".m4a",
    ".m4b",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
    ".wma",
}
voice_loop_tasks: Dict[int, asyncio.Task] = {}
voice_now_playing: Dict[int, Dict[str, Any]] = {}
voice_connect_lock: Optional[asyncio.Lock] = None
voice_connect_locks: Dict[int, asyncio.Lock] = {}
VOICE_FAILURES_PATH = LOG_DIR / "voice_failures.json"
VOICE_EVENTS_RECENT_PATH = LOG_DIR / "voice_events_recent.json"
VOICE_FAILURE_WINDOW_DAYS = 7
VOICE_EVENTS_RECENT_MAX = 1200
voice_event_lock = threading.Lock()
server_room_bleed_last_signature: Dict[int, str] = {}
server_room_frame_stats: Dict[int, Dict[str, float]] = {}
server_room_env_cache: Dict[str, Any] = {"collected_at": 0.0, "data": {}}
server_room_env_lock = threading.Lock()
SERVER_ROOM_ENV_REFRESH_S = 30.0
SERVER_ROOM_MESSAGE_MAX_CHARS = 2000
SERVER_ROOM_MESSAGE_TARGET_CHARS = 1980
SERVER_ROOM_MESSAGE_TARGET_VERBOSE_CHARS = 1992
SERVER_ROOM_MAX_EDITS_PER_SECOND = 2.0
SERVER_ROOM_BURST_EDITS_PER_SECOND = 4.0
SERVER_ROOM_PAUSED_EDITS_PER_SECOND = 0.5
SERVER_ROOM_BURST_DURATION_S = 18.0
SERVER_ROOM_REFRESH_BLANK_DURATION_S = 4.0
SERVER_ROOM_REBOOT_DURATION_S = 8.0
SERVER_ROOM_REBOOT_COUNTDOWN_S = 13.0
SERVER_ROOM_REACTION_REFRESH_S = 8.0
SERVER_ROOM_REACTION_NORMALIZE_S = 18.0
SERVER_ROOM_INTERRUPT_DEBOUNCE_S = 0.35
SERVER_ROOM_CONTROLLER_LOCK_S = 15.0
SERVER_ROOM_DIFF_SIMILARITY_SKIP = 0.985
SERVER_ROOM_RATE_LIMIT_BACKOFF_BASE_S = 2.0
SERVER_ROOM_RATE_LIMIT_BACKOFF_MAX_S = 32.0
CHANNEL_MEMORY_FETCH_LIMIT = 900
CHANNEL_MEMORY_SAMPLE_SIZE = 240
MASTERBOT_CLI_CHANNEL_ID = 632804257317519370
MASTERBOT_CLI_MODES = ["normal", "stress", "entropy", "diagnostic"]
MASTERBOT_CLI_CURSOR_STATES = ["_", " ", "▁", " ", "▂", " ", "▃", " "]
MASTERBOT_CLI_REACTION_COMMANDS: Dict[str, str] = {
    "🟢": "resume",
    "⏸": "pause",
    "⚡": "burst",
    "🧪": "verbose",
    "🔁": "refresh",
    "🧹": "clearUI",
    "♻": "cycleMode",
    "❌": "abort",
    "🗂": "memory",
    "🧾": "analysis",
    "🧠": "hint",
}
MASTERBOT_CLI_REACTION_EMOJIS = ["🟢", "⏸️", "⚡", "🧪", "🔁", "🧹", "♻️", "❌", "🗂️", "🧾", "🧠"]
MASTERBOT_CLI_MEMORY_PATH = LOG_DIR / "masterbot_cli_memory.json"
MASTERBOT_CLI_LLM_CACHE_PATH = LOG_DIR / "masterbot_cli_llm_cache.json"
LLM_PROVIDER_DEFAULT = "openai"
LLM_MODEL_DEFAULT = "gpt-4.1-mini"
HA_BASE_URL = "https://ha.system42.one"
HA_SENSOR_PREFS: Dict[str, List[str]] = {
    "ambient_temp": [
        "sensor.the_lab_climate_sensor_temperature",
        "sensor.average_household_temperature",
        "sensor.thermostat_temp",
    ],
    "particulate": [
        "sensor.levoit_smart_true_hepa_air_purifier_pm2_5",
    ],
    "ambient_light": [
        "sensor.apollo_mtr_1_12022c_ltr390_light",
        "sensor.frontend_tablet_light_sensor",
        "sensor.majesty_palm_illuminance",
    ],
    "apollo_co2": [
        "sensor.apollo_mtr_1_12022c_co2",
    ],
    "apollo_pressure": [
        "sensor.apollo_mtr_1_12022c_dps310_pressure",
    ],
    "apollo_dps_temp": [
        "sensor.apollo_mtr_1_12022c_dps310_temperature",
    ],
    "apollo_esp_temp": [
        "sensor.apollo_mtr_1_12022c_esp_temperature",
    ],
    "apollo_uv": [
        "sensor.apollo_mtr_1_12022c_ltr390_uv_index",
    ],
    "apollo_rssi": [
        "sensor.apollo_mtr_1_12022c_rssi",
    ],
    "apollo_uptime": [
        "sensor.apollo_mtr_1_12022c_uptime",
    ],
    "apollo_online": [
        "binary_sensor.apollo_mtr_1_12022c_online",
    ],
    "apollo_presence": [
        "sensor.apollo_mtr_1_12022c_presence_target_count",
    ],
    "apollo_moving": [
        "sensor.apollo_mtr_1_12022c_moving_target_count",
    ],
    "apollo_still": [
        "sensor.apollo_mtr_1_12022c_still_target_count",
    ],
    "apollo_zone_1_all": [
        "sensor.apollo_mtr_1_12022c_zone_1_all_target_count",
    ],
    "apollo_zone_2_all": [
        "sensor.apollo_mtr_1_12022c_zone_2_all_target_count",
    ],
    "apollo_zone_3_all": [
        "sensor.apollo_mtr_1_12022c_zone_3_all_target_count",
    ],
    "apollo_zone_1_moving": [
        "sensor.apollo_mtr_1_12022c_zone_1_moving_target_count",
    ],
    "apollo_zone_2_moving": [
        "sensor.apollo_mtr_1_12022c_zone_2_moving_target_count",
    ],
    "apollo_zone_3_moving": [
        "sensor.apollo_mtr_1_12022c_zone_3_moving_target_count",
    ],
    "apollo_zone_1_still": [
        "sensor.apollo_mtr_1_12022c_zone_1_still_target_count",
    ],
    "apollo_zone_2_still": [
        "sensor.apollo_mtr_1_12022c_zone_2_still_target_count",
    ],
    "apollo_zone_3_still": [
        "sensor.apollo_mtr_1_12022c_zone_3_still_target_count",
    ],
}
voice_preset_autostart_done = False
SERVER_ROOM_STATUS_CHANNEL_IDS_DEFAULT = [
    496375061134049294,  # Electronics / server room
    632804257317519370,  # D&D / masterbot-cli
    1109584160047247420,  # Deer Valley / share board
]
SERVER_ROOM_STATUS_CHANNEL_IDS = list(SERVER_ROOM_STATUS_CHANNEL_IDS_DEFAULT)
SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS_DEFAULT = {
    496375061134049294: 1476684359665979533,  # Electronics / server room pinned status message
    632804257317519370: 1476761935050571907,  # reuse existing masterbot-cli status message
    1109584160047247420: 1386422860758913037,  # Deer Valley / share board status message
}
SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS = dict(SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS_DEFAULT)
SERVER_ROOM_BACKUP_WORD = "BACKUP"
server_room_status_task: Optional[asyncio.Task] = None
DEFAULT_VISITOR_ROLE_ID_DEFAULT = 1477727516138672208
DEFAULT_VISITOR_GUILD_ID_DEFAULT = 1072011626066231376  # Deer Valley
VISITOR_ROLE_EXEMPT_USER_IDS_DEFAULT = {251038279648935936}  # Thomas
DEFAULT_VISITOR_ROLE_ID = DEFAULT_VISITOR_ROLE_ID_DEFAULT
DEFAULT_VISITOR_GUILD_ID = DEFAULT_VISITOR_GUILD_ID_DEFAULT
VISITOR_ROLE_EXEMPT_USER_IDS = set(VISITOR_ROLE_EXEMPT_USER_IDS_DEFAULT)
server_room_active_message_ids: Dict[int, int] = {}
server_room_render_state: Dict[int, Dict[str, Any]] = {}
server_room_reaction_maintenance: Dict[int, Dict[str, float]] = {}
masterbot_soft_reboot_task: Optional[asyncio.Task] = None
server_room_cli_memory_loaded = False
server_room_cli_control: Dict[str, Any] = {
    "paused": False,
    "verbose": False,
    "mode": "normal",
    "abort": False,
    "burst_until": 0.0,
    "clear_ui": False,
    "refresh_requested": False,
    "tick": 0,
    "banner": "",
    "banner_until": 0.0,
    "audit": [],
    "clear_mode": False,
    "refresh_blank_until": 0.0,
    "reboot_started_at": 0.0,
    "reboot_until": 0.0,
    "reboot_countdown_started_at": 0.0,
    "reboot_countdown_until": 0.0,
    "soft_reboot_in_progress": False,
    "interrupt_queue": [],
    "interrupt_seq": 0,
    "interrupt_last_ts": {},
    "active_controller_user_id": 0,
    "active_controller_until": 0.0,
    "last_controller_user_id": 0,
    "show_memory_until": 0.0,
    "network_status": "nominal",
    "rate_limit_backoff_until": 0.0,
    "rate_limit_hits": 0,
    "session_started_at": int(time.time()),
    "command_heatmap": {},
    "mode_history": [],
    "session_count": 1,
    "llm_analysis_line": "",
    "llm_analysis_until": 0.0,
    "llm_notice_until": 0.0,
    "llm_offline_notice_until": 0.0,
    "llm_analyst_text": "",
    "llm_analyst_until": 0.0,
    "llm_task": None,
    "llm_manual_request": None,
    "llm_last_event_hash": "",
    "llm_last_semantic_state": {},
    "llm_failures": 0,
    "llm_enabled": False,
}


class TelemetrySource:
    def collect(self) -> Dict[str, Any]:
        return {}


class SystemTelemetrySource(TelemetrySource):
    def collect(self) -> Dict[str, Any]:
        return {
            "system_uptime_s": _read_system_uptime_s(),
            "cpu_temp_c": _read_cpu_temp_c(),
            "gpu_temp_c": _read_gpu_temp_c(),
        }


class HomeAssistantTelemetrySource(TelemetrySource):
    def collect(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        token = _ha_token()
        if not token:
            return {"ha_error": "HOME_ASSISTANT_TOKEN missing"}
        data["ha_ambient"] = _ha_first_available(token, HA_SENSOR_PREFS["ambient_temp"])
        data["ha_particulate"] = _ha_first_available(token, HA_SENSOR_PREFS["particulate"])
        data["ha_light"] = _ha_first_available(token, HA_SENSOR_PREFS["ambient_light"])
        data["ha_apollo_co2"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_co2"])
        data["ha_apollo_pressure"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_pressure"])
        data["ha_apollo_dps_temp"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_dps_temp"])
        data["ha_apollo_esp_temp"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_esp_temp"])
        data["ha_apollo_uv"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_uv"])
        data["ha_apollo_rssi"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_rssi"])
        data["ha_apollo_uptime"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_uptime"])
        data["ha_apollo_online"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_online"])
        data["ha_apollo_presence"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_presence"])
        data["ha_apollo_moving"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_moving"])
        data["ha_apollo_still"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_still"])
        data["ha_apollo_zone_1_all"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_1_all"])
        data["ha_apollo_zone_2_all"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_2_all"])
        data["ha_apollo_zone_3_all"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_3_all"])
        data["ha_apollo_zone_1_moving"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_1_moving"])
        data["ha_apollo_zone_2_moving"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_2_moving"])
        data["ha_apollo_zone_3_moving"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_3_moving"])
        data["ha_apollo_zone_1_still"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_1_still"])
        data["ha_apollo_zone_2_still"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_2_still"])
        data["ha_apollo_zone_3_still"] = _ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_3_still"])
        return data


SERVER_ROOM_TELEMETRY_SOURCES: List[TelemetrySource] = [
    SystemTelemetrySource(),
    HomeAssistantTelemetrySource(),
]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool
    provider: str
    model: str
    cooldown_s: float
    cache_enabled: bool
    cache_ttl_s: float
    max_chars: Dict[str, int]
    max_lines: Dict[str, int]

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            enabled=_env_bool("LLM_ENABLED", default=True),
            provider=os.getenv("LLM_PROVIDER", LLM_PROVIDER_DEFAULT).strip() or LLM_PROVIDER_DEFAULT,
            model=os.getenv("LLM_MODEL", LLM_MODEL_DEFAULT).strip() or LLM_MODEL_DEFAULT,
            cooldown_s=max(1.0, float(_env_int("LLM_COOLDOWN_S", 8))),
            cache_enabled=_env_bool("LLM_CACHE_ENABLED", default=True),
            cache_ttl_s=max(15.0, float(_env_int("LLM_CACHE_TTL_S", 300))),
            max_chars={
                "explain_transition": max(120, _env_int("LLM_MAX_CHARS_EXPLAIN", 220)),
                "summarize_tail": max(180, _env_int("LLM_MAX_CHARS_SUMMARY", 400)),
                "operator_hint": max(80, _env_int("LLM_MAX_CHARS_HINT", 120)),
            },
            max_lines={
                "explain_transition": 2,
                "summarize_tail": 4,
                "operator_hint": 1,
            },
        )


class LLMClient:
    def __init__(self, config: LLMConfig, client: OpenAI) -> None:
        self.config = config
        self.client = client
        self.system_prompt = (
            "You are an operations-console analyst for a deterministic runtime. "
            "Use only provided keys. Never invent sensors. No markdown or code fences. "
            "Keep output concise and operational."
        )

    def _call(self, endpoint: str, payload: Dict[str, Any], max_chars: int, max_lines: int) -> str:
        if not self.config.enabled:
            return ""
        if self.config.provider != "openai":
            return ""

        endpoint_rules = {
            "explain_transition": "Return 1-2 lines, start with 'analysis:' or 'notice:'.",
            "summarize_tail": "Return 3-4 lines: what happened, likely cause, next step.",
            "operator_hint": "Return exactly 1 line in format 'next: <action>'.",
        }
        user_text = json.dumps(
            {
                "endpoint": endpoint,
                "constraints": {"max_chars": max_chars, "max_lines": max_lines},
                "payload": payload,
            },
            sort_keys=True,
        )
        try:
            if hasattr(self.client, "responses"):
                resp = self.client.responses.create(
                    model=self.config.model,
                    input=[
                        {"role": "system", "content": [{"type": "input_text", "text": self.system_prompt}]},
                        {"role": "system", "content": [{"type": "input_text", "text": endpoint_rules.get(endpoint, "")}]},
                        {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
                    ],
                    temperature=0.1,
                )
                return (resp.output_text or "").strip()

            resp = self.client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "system", "content": endpoint_rules.get(endpoint, "")},
                    {"role": "user", "content": user_text},
                ],
                temperature=0.1,
            )
            content = resp.choices[0].message.content if resp.choices else ""
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = [item.get("text", "") for item in content if isinstance(item, dict)]
                return "\n".join(part for part in parts if part).strip()
        except Exception:
            logger.exception("LLM call failed endpoint=%s", endpoint)
        return ""


class LLMBus:
    def __init__(self, config: LLMConfig, client: LLMClient, cache_path: Path) -> None:
        self.config = config
        self.client = client
        self.cache_path = cache_path
        self.endpoint_last_call: Dict[str, float] = {}
        self.endpoint_last_hash: Dict[str, str] = {}
        self.cache_mem: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.cache_max_entries = 96
        self.offline_noted_at = 0.0
        self.failures = 0
        self._load_cache()

    def _load_cache(self) -> None:
        if not self.config.cache_enabled or not self.cache_path.exists():
            return
        try:
            obj = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                return
            now = time.time()
            for key, item in obj.items():
                if not isinstance(item, dict):
                    continue
                expire_at = float(item.get("expire_at", 0.0) or 0.0)
                value = str(item.get("value", "") or "")
                if not value or expire_at <= now:
                    continue
                self.cache_mem[key] = {"value": value, "expire_at": expire_at}
            while len(self.cache_mem) > self.cache_max_entries:
                self.cache_mem.popitem(last=False)
        except Exception:
            logger.exception("LLM cache load failed path=%s", self.cache_path)

    def _save_cache(self) -> None:
        if not self.config.cache_enabled:
            return
        try:
            payload = dict(self.cache_mem)
            tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            tmp.replace(self.cache_path)
        except Exception:
            logger.exception("LLM cache save failed path=%s", self.cache_path)

    def _input_hash(self, payload: Dict[str, Any]) -> str:
        txt = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(txt.encode("utf-8")).hexdigest()

    def _sanitize(self, endpoint: str, raw: str) -> str:
        if not raw:
            return ""
        max_lines = int(self.config.max_lines.get(endpoint, 2))
        max_chars = int(self.config.max_chars.get(endpoint, 220))
        lines = [ln.strip() for ln in raw.replace("\r", "").split("\n") if ln.strip()]
        clipped_lines = lines[:max_lines]
        text = "\n".join(clipped_lines)
        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + "..."
        return text

    def _fetch_cache(self, key: str) -> str:
        item = self.cache_mem.get(key)
        if not item:
            return ""
        if float(item.get("expire_at", 0.0) or 0.0) <= time.time():
            self.cache_mem.pop(key, None)
            return ""
        self.cache_mem.move_to_end(key)
        return str(item.get("value", "") or "")

    def _store_cache(self, key: str, value: str) -> None:
        if not self.config.cache_enabled or not value:
            return
        expire_at = time.time() + self.config.cache_ttl_s
        self.cache_mem[key] = {"value": value, "expire_at": expire_at}
        self.cache_mem.move_to_end(key)
        while len(self.cache_mem) > self.cache_max_entries:
            self.cache_mem.popitem(last=False)
        self._save_cache()

    def _call_endpoint(self, endpoint: str, payload: Dict[str, Any]) -> str:
        if not self.config.enabled:
            return ""

        now = time.monotonic()
        call_hash = self._input_hash(payload)
        cache_key = f"{endpoint}:{call_hash}"
        last_ts = float(self.endpoint_last_call.get(endpoint, 0.0) or 0.0)
        last_hash = str(self.endpoint_last_hash.get(endpoint, "") or "")
        cooldown_active = (now - last_ts) < self.config.cooldown_s

        if cooldown_active:
            if call_hash == last_hash:
                return self._fetch_cache(cache_key)
            return ""

        cached = self._fetch_cache(cache_key)
        if cached:
            self.endpoint_last_call[endpoint] = now
            self.endpoint_last_hash[endpoint] = call_hash
            return cached

        raw = self.client._call(
            endpoint=endpoint,
            payload=payload,
            max_chars=int(self.config.max_chars.get(endpoint, 220)),
            max_lines=int(self.config.max_lines.get(endpoint, 2)),
        )
        text = self._sanitize(endpoint, raw)
        self.endpoint_last_call[endpoint] = now
        self.endpoint_last_hash[endpoint] = call_hash
        if text:
            self._store_cache(cache_key, text)
            self.failures = 0
        else:
            self.failures += 1
        return text

    def explain_transition(
        self,
        prev_state: Dict[str, Any],
        new_state: Dict[str, Any],
        triggers: List[str],
        telemetry_digest: Dict[str, Any],
    ) -> str:
        payload = {
            "prev_state": prev_state,
            "new_state": new_state,
            "triggers": triggers[:8],
            "telemetry": telemetry_digest,
        }
        return self._call_endpoint("explain_transition", payload)

    def summarize_tail(self, lines: List[str], telemetry_digest: Dict[str, Any], mode: str) -> str:
        payload = {
            "mode": mode,
            "tail": lines[-24:],
            "telemetry": telemetry_digest,
        }
        return self._call_endpoint("summarize_tail", payload)

    def operator_hint(self, context_capsule: Dict[str, Any]) -> str:
        payload = {"context": context_capsule}
        return self._call_endpoint("operator_hint", payload)


llm_config = LLMConfig.from_env()
llm_bus = LLMBus(llm_config, LLMClient(llm_config, openai_client), MASTERBOT_CLI_LLM_CACHE_PATH)
server_room_cli_control["llm_enabled"] = bool(llm_config.enabled)


def _parse_numeric_state(payload: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    raw = str(payload.get("state", "") or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _server_room_llm_enabled() -> bool:
    runtime_flag = bool(server_room_cli_control.get("llm_enabled", False))
    return bool(llm_config.enabled and runtime_flag)


def build_telemetry_digest(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    uptime_s = snapshot.get("system_uptime_s")
    digest = {
        "cpu_temp_c": snapshot.get("cpu_temp_c"),
        "gpu_temp_c": snapshot.get("gpu_temp_c"),
        "pm25": _parse_numeric_state(snapshot.get("ha_particulate")),
        "co2": _parse_numeric_state(snapshot.get("ha_apollo_co2")),
        "ambient_temp": _parse_numeric_state(snapshot.get("ha_ambient")),
        "ambient_light": _parse_numeric_state(snapshot.get("ha_light")),
        "uptime_s": int(uptime_s) if isinstance(uptime_s, int) else None,
        "fps_avg": None,
        "network_status": str(server_room_cli_control.get("network_status", "nominal")),
    }
    cli_stats = dict(server_room_frame_stats.get(MASTERBOT_CLI_CHANNEL_ID, {}))
    fps = cli_stats.get("fps_ema")
    if isinstance(fps, (int, float)):
        digest["fps_avg"] = round(float(fps), 2)
    return digest


def build_transition_triggers(prev_state: Dict[str, Any], new_state: Dict[str, Any]) -> List[str]:
    keys = ("paused", "mode", "burst_active", "reboot_countdown", "reboot_active", "clear_mode", "network_status")
    triggers: List[str] = []
    for key in keys:
        if prev_state.get(key) != new_state.get(key):
            triggers.append(f"{key}:{prev_state.get(key)}->{new_state.get(key)}")
    anomalies = list(new_state.get("anomalies", []))
    prev_anomalies = set(prev_state.get("anomalies", []))
    for item in anomalies:
        if item not in prev_anomalies:
            triggers.append(f"anomaly:{item}")
    return triggers


def build_operator_capsule(
    state: Dict[str, Any],
    audit_tail: List[Dict[str, Any]],
    telemetry_digest: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "state": state,
        "audit_tail": [
            {
                "ts": str(item.get("ts", "")),
                "user_id": int(item.get("user_id", 0) or 0),
                "cmd": str(item.get("cmd", "")),
            }
            for item in audit_tail[-8:]
            if isinstance(item, dict)
        ],
        "telemetry": telemetry_digest,
    }


def _server_room_semantic_state(now: float, env: Dict[str, Any]) -> Dict[str, Any]:
    with server_room_env_lock:
        collected_at = float(server_room_env_cache.get("collected_at", 0.0) or 0.0)
    stale = collected_at > 0.0 and (time.time() - collected_at) > (SERVER_ROOM_ENV_REFRESH_S * 2.5)
    cpu_temp = env.get("cpu_temp_c")
    gpu_temp = env.get("gpu_temp_c")
    pm25 = _parse_numeric_state(env.get("ha_particulate"))
    anomalies: List[str] = []
    if stale:
        anomalies.append("telemetry_stale")
    if isinstance(cpu_temp, (int, float)) and float(cpu_temp) >= 85.0:
        anomalies.append("cpu_hot")
    if isinstance(gpu_temp, (int, float)) and float(gpu_temp) >= 85.0:
        anomalies.append("gpu_hot")
    if isinstance(pm25, (int, float)) and float(pm25) >= 35.0:
        anomalies.append("pm25_spike")
    if float(server_room_cli_control.get("rate_limit_backoff_until", 0.0) or 0.0) > now:
        anomalies.append("throttle_backoff")

    return {
        "paused": bool(server_room_cli_control.get("paused", False)),
        "mode": str(server_room_cli_control.get("mode", MASTERBOT_CLI_MODES[0])),
        "burst_active": _server_room_cli_is_burst_active(now),
        "reboot_countdown": _server_room_cli_is_reboot_countdown_active(now),
        "reboot_active": _server_room_cli_is_reboot_active(now),
        "clear_mode": bool(server_room_cli_control.get("clear_mode", False)),
        "network_status": str(server_room_cli_control.get("network_status", "nominal")),
        "anomalies": anomalies,
    }


def _server_room_read_log_tail(path: Path, max_lines: int = 40) -> List[str]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-max_lines:]


def _tokenize_summary_words(text: str) -> List[str]:
    if not text:
        return []
    stop = {
        "the",
        "and",
        "for",
        "that",
        "with",
        "this",
        "from",
        "have",
        "just",
        "your",
        "you",
        "are",
        "was",
        "were",
        "but",
        "not",
        "all",
        "can",
        "its",
        "they",
        "them",
        "out",
        "about",
        "into",
        "there",
        "their",
        "will",
        "what",
        "when",
        "where",
        "why",
        "how",
        "http",
        "https",
    }
    cleaned = text.lower().translate(str.maketrans("", "", string.punctuation))
    parts = [p for p in cleaned.split() if len(p) >= 4 and p not in stop and not p.isdigit()]
    return parts


async def _server_room_collect_channel_messages(
    channel_id: int,
    max_messages: int = CHANNEL_MEMORY_FETCH_LIMIT,
) -> Tuple[List[Dict[str, str]], bool]:
    channel = await _server_room_resolve_text_channel(channel_id)
    if channel is None:
        return [], False
    items: List[Dict[str, str]] = []
    truncated = False
    try:
        scanned = 0
        async for msg in channel.history(limit=max_messages, oldest_first=True):
            scanned += 1
            content = (msg.content or "").strip()
            if not content:
                continue
            items.append(
                {
                    "ts": msg.created_at.strftime("%Y-%m-%d"),
                    "author": getattr(msg.author, "display_name", getattr(msg.author, "name", "unknown")),
                    "content": _clip_text(content.replace("\n", " "), 220),
                }
            )
        truncated = scanned >= max_messages
    except Exception:
        logger.exception("CHANNEL_MEMORY failed to read history channel=%s", channel_id)
    if len(items) <= CHANNEL_MEMORY_SAMPLE_SIZE:
        return items, truncated
    rng = random.Random(time.time_ns() ^ int(channel_id))
    selected = sorted(rng.sample(range(len(items)), CHANNEL_MEMORY_SAMPLE_SIZE))
    sampled = [items[idx] for idx in selected]
    return sampled, True


def _server_room_build_channel_memory_capsule(
    channel_id: int,
    messages: List[Dict[str, str]],
    truncated: bool,
) -> List[str]:
    if not messages:
        return [f"channel_id={channel_id}", "no_message_content=true"]

    authors = Counter(str(m.get("author", "unknown")) for m in messages)
    words = Counter()
    for msg in messages:
        words.update(_tokenize_summary_words(str(msg.get("content", ""))))
    top_authors = ", ".join(f"{name}:{count}" for name, count in authors.most_common(6)) or "none"
    top_words = ", ".join(word for word, _ in words.most_common(10)) or "none"

    lines: List[str] = [
        f"channel_id={channel_id}",
        f"messages={len(messages)} unique_authors={len(authors)} truncated={truncated}",
        f"timespan={messages[0].get('ts','?')}..{messages[-1].get('ts','?')}",
        f"top_authors={top_authors}",
        f"top_terms={top_words}",
    ]
    sample_count = min(14, len(messages))
    if sample_count <= 0:
        return lines
    step = max(1, len(messages) // sample_count)
    for idx in range(0, len(messages), step):
        msg = messages[idx]
        lines.append(
            f"{msg.get('ts','?')} {msg.get('author','unknown')}: {_clip_text(str(msg.get('content','')), 120)}"
        )
        if len(lines) >= 24:
            break
    return lines


def _server_room_set_analyst_panel(text: str, ttl_s: float = 35.0) -> None:
    if not text:
        return
    server_room_cli_control["llm_analyst_text"] = text
    server_room_cli_control["llm_analyst_until"] = time.monotonic() + max(6.0, ttl_s)


async def _server_room_handle_llm_manual_request(env_snapshot: Dict[str, Any]) -> None:
    request = server_room_cli_control.get("llm_manual_request")
    if not isinstance(request, dict):
        return
    server_room_cli_control["llm_manual_request"] = None
    cmd = str(request.get("cmd", "") or "")
    user_id = int(request.get("user_id", 0) or 0)
    request_channel_id = int(request.get("channel_id", 0) or 0)
    if cmd == "analysis":
        _server_room_set_analyst_panel("analysis: gathering incident report...", ttl_s=8.0)
    elif cmd == "hint":
        _server_room_set_analyst_panel("next: gathering operator hint...", ttl_s=8.0)
    elif cmd == "memory":
        _server_room_set_analyst_panel("analysis: scanning channel memory...", ttl_s=10.0)
    if not _server_room_llm_enabled():
        _server_room_set_analyst_panel("analysis: LLM disabled (set LLM_ENABLED=1).", ttl_s=18.0)
        return

    telemetry_digest = build_telemetry_digest(env_snapshot)
    mode = str(server_room_cli_control.get("mode", MASTERBOT_CLI_MODES[0]))
    if cmd == "analysis":
        tail = _server_room_read_log_tail(LOG_DIR / "masterbot.log", max_lines=40)
        summary = ""
        try:
            summary = await asyncio.wait_for(
                asyncio.to_thread(llm_bus.summarize_tail, tail, telemetry_digest, mode),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("LLM summarize_tail timed out user=%s", user_id)
        if summary:
            _server_room_set_analyst_panel(summary)
            _server_room_cli_emit_banner(f"analyst report generated for user={user_id}", ttl_s=8.0)
            logger.info("LLM analyst report ready user=%s chars=%s", user_id, len(summary))
        else:
            _server_room_set_analyst_panel("notice: analysis cooldown active or provider unavailable.", ttl_s=12.0)
    elif cmd == "hint":
        state = _server_room_semantic_state(time.monotonic(), env_snapshot)
        audit = list(server_room_cli_control.get("audit", []))
        capsule = build_operator_capsule(state, audit, telemetry_digest)
        hint = ""
        try:
            hint = await asyncio.wait_for(
                asyncio.to_thread(llm_bus.operator_hint, capsule),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("LLM operator_hint timed out user=%s", user_id)
        if hint:
            _server_room_set_analyst_panel(hint, ttl_s=22.0)
            _server_room_cli_emit_banner(f"operator hint ready for user={user_id}", ttl_s=8.0)
            logger.info("LLM operator hint ready user=%s chars=%s", user_id, len(hint))
        else:
            _server_room_set_analyst_panel("next: wait for cooldown window and request again.", ttl_s=12.0)
    elif cmd == "memory":
        channel_id = request_channel_id or MASTERBOT_CLI_CHANNEL_ID
        try:
            messages, truncated = await asyncio.wait_for(
                _server_room_collect_channel_messages(channel_id, max_messages=CHANNEL_MEMORY_FETCH_LIMIT),
                timeout=12.0,
            )
        except asyncio.TimeoutError:
            logger.warning("CHANNEL_MEMORY history collection timed out channel=%s", channel_id)
            _server_room_set_analyst_panel("notice: channel history scan timed out.", ttl_s=12.0)
            return
        capsule_lines = _server_room_build_channel_memory_capsule(channel_id, messages, truncated)
        summary = ""
        try:
            summary = await asyncio.wait_for(
                asyncio.to_thread(llm_bus.summarize_tail, capsule_lines, telemetry_digest, "channel-memory"),
                timeout=12.0,
            )
        except asyncio.TimeoutError:
            logger.warning("LLM channel memory summarize timed out user=%s channel=%s", user_id, channel_id)
        if summary:
            _server_room_set_analyst_panel(summary, ttl_s=28.0)
            _server_room_cli_emit_banner(f"channel memory summary ready (channel={channel_id})", ttl_s=8.0)
            logger.info(
                "LLM channel memory summary ready user=%s channel=%s msgs=%s truncated=%s chars=%s",
                user_id,
                channel_id,
                len(messages),
                truncated,
                len(summary),
            )
        else:
            top_line = capsule_lines[1] if len(capsule_lines) > 1 else f"messages={len(messages)}"
            _server_room_set_analyst_panel(
                f"notice: channel memory summary unavailable.\n{_clip_text(top_line, 120)}",
                ttl_s=14.0,
            )


async def _server_room_emit_llm_transition_if_needed(env_snapshot: Dict[str, Any]) -> None:
    now = time.monotonic()
    new_state = _server_room_semantic_state(now, env_snapshot)
    prev_state = dict(server_room_cli_control.get("llm_last_semantic_state", {}) or {})
    if not prev_state:
        server_room_cli_control["llm_last_semantic_state"] = new_state
        return
    triggers = build_transition_triggers(prev_state, new_state)
    if not triggers:
        return

    event_payload = {
        "prev": prev_state,
        "new": new_state,
        "triggers": triggers[:8],
        "telemetry": build_telemetry_digest(env_snapshot),
    }
    event_hash = hashlib.sha1(json.dumps(event_payload, sort_keys=True).encode("utf-8")).hexdigest()
    if event_hash == str(server_room_cli_control.get("llm_last_event_hash", "") or ""):
        server_room_cli_control["llm_last_semantic_state"] = new_state
        return
    server_room_cli_control["llm_last_event_hash"] = event_hash
    server_room_cli_control["llm_last_semantic_state"] = new_state

    if not _server_room_llm_enabled():
        return

    narration = await asyncio.to_thread(
        llm_bus.explain_transition,
        prev_state,
        new_state,
        triggers,
        event_payload["telemetry"],
    )
    if narration:
        text = narration.replace("\n", " ").strip()
        text = _clip_text(text, llm_config.max_chars["explain_transition"])
        server_room_cli_control["llm_analysis_line"] = f"[analysis] {text}"
        server_room_cli_control["llm_analysis_until"] = now + 14.0
        return

    if llm_bus.failures and (now - float(server_room_cli_control.get("llm_offline_notice_until", 0.0) or 0.0) > 0.0):
        server_room_cli_control["llm_analysis_line"] = "[analysis] LLM offline; falling back to deterministic logs"
        server_room_cli_control["llm_analysis_until"] = now + 12.0
        server_room_cli_control["llm_offline_notice_until"] = now + 90.0


async def _server_room_llm_cycle(env_snapshot: Dict[str, Any]) -> None:
    try:
        await _server_room_handle_llm_manual_request(env_snapshot)
        await _server_room_emit_llm_transition_if_needed(env_snapshot)
    except Exception:
        logger.exception("SERVER_ROOM LLM cycle failed")


def _server_room_schedule_llm_cycle(env_snapshot: Dict[str, Any]) -> None:
    if not _server_room_llm_enabled() and not server_room_cli_control.get("llm_manual_request"):
        return
    task = server_room_cli_control.get("llm_task")
    if isinstance(task, asyncio.Task) and not task.done():
        return
    snapshot_copy = dict(env_snapshot)
    server_room_cli_control["llm_task"] = asyncio.create_task(
        _server_room_llm_cycle(snapshot_copy),
        name="server-room-llm-cycle",
    )


def _server_room_cli_memory_load() -> None:
    path = MASTERBOT_CLI_MEMORY_PATH
    if not path.exists():
        return
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return
        audit = obj.get("audit")
        if isinstance(audit, list):
            server_room_cli_control["audit"] = audit[-80:]
        heatmap = obj.get("command_heatmap")
        if isinstance(heatmap, dict):
            server_room_cli_control["command_heatmap"] = {str(k): int(v) for k, v in heatmap.items()}
        mode_history = obj.get("mode_history")
        if isinstance(mode_history, list):
            server_room_cli_control["mode_history"] = mode_history[-40:]
        # Compatibility migration: move old JSON-backed debug channels into runtime_config in shelve.
        migrated = False
        dynamic_channels = obj.get("dynamic_status_channels")
        if isinstance(dynamic_channels, list):
            for raw in dynamic_channels:
                try:
                    ch_id = int(raw)
                except Exception:
                    continue
                if ch_id not in SERVER_ROOM_STATUS_CHANNEL_IDS:
                    SERVER_ROOM_STATUS_CHANNEL_IDS.append(ch_id)
                    migrated = True
        dynamic_pins = obj.get("dynamic_status_pins")
        if isinstance(dynamic_pins, dict):
            for raw_ch, raw_msg in dynamic_pins.items():
                try:
                    ch_id = int(raw_ch)
                    msg_id = int(raw_msg)
                except Exception:
                    continue
                if SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS.get(ch_id) != msg_id:
                    SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS[ch_id] = msg_id
                    migrated = True
        if migrated:
            _runtime_config_save()
        sessions = int(obj.get("session_count", 0) or 0)
        server_room_cli_control["session_count"] = max(1, sessions + 1)
    except Exception:
        logger.exception("CLI memory load failed path=%s", path)


def _server_room_cli_memory_save() -> None:
    path = MASTERBOT_CLI_MEMORY_PATH
    payload = {
        "saved_at": int(time.time()),
        "audit": list(server_room_cli_control.get("audit", []))[-80:],
        "command_heatmap": dict(server_room_cli_control.get("command_heatmap", {})),
        "mode_history": list(server_room_cli_control.get("mode_history", []))[-40:],
        "session_count": int(server_room_cli_control.get("session_count", 1) or 1),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logger.exception("CLI memory save failed path=%s", path)


class SolEngine:
    def __init__(self, client: OpenAI) -> None:
        self.client = client
        self.index_ready = False
        self.is_building = False
        self.build_progress = 0.0
        self.docs: List[Dict[str, Any]] = []
        self.embeddings: List[List[float]] = []
        self.model = "text-embedding-3-small"
        self.chunk_size = 900
        self._state_lock = threading.Lock()

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> List[str]:
        text = text.strip()
        if not text:
            return []
        chunks: List[str] = []
        start = 0
        step = max(1, chunk_size - overlap)
        while start < len(text):
            chunks.append(text[start : start + chunk_size])
            start += step
        return chunks

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(y * y for y in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def _discover_files(self) -> List[Path]:
        env_path = os.getenv("MASTERBOT_KNOWLEDGE_DIR", "").strip()
        script_path = Path(__file__).resolve()
        candidates: List[Path] = []
        if env_path:
            candidates.append(Path(env_path).expanduser())
        candidates.extend(
            [
                Path.cwd() / "knowledge",
                script_path.parent.parent / "knowledge",
                script_path.parent.parent.parent / "knowledge",
            ]
        )

        knowledge: Optional[Path] = None
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                knowledge = candidate
                break

        if knowledge is None:
            tried = ", ".join(str(p) for p in candidates)
            logger.warning(f"SOL: knowledge directory not found (tried: {tried}). No indexing will occur.")
            return []

        logger.info("SOL: using knowledge directory: %s", knowledge)

        files: List[Path] = []
        for p in knowledge.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".txt", ".md", ".markdown"}:
                files.append(p)

        return files

    def _load_text_files(self) -> List[Tuple[str, float, str]]:
        corpus: List[Tuple[str, float, str]] = []
        for p in self._discover_files():
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                if content.strip():
                    corpus.append((str(p), p.stat().st_mtime, content))
            except Exception:
                continue
        return corpus

    def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        total = len(texts)
        if total == 0:
            return vectors

        batches = [(i, texts[i : i + 32]) for i in range(0, total, 32)]
        max_workers = min(4, len(batches))

        def embed_one(batch_item: Tuple[int, List[str]]) -> Tuple[int, List[List[float]]]:
            i, batch = batch_item
            logger.info(f"SOL:  Embedding batch {i//32 + 1} ({i+1}-{min(i+32, total)}/{total})")
            result = self.client.embeddings.create(model=self.model, input=batch)
            return i, [list(item.embedding) for item in result.data]

        if max_workers <= 1:
            for batch_item in batches:
                _, chunk_vectors = embed_one(batch_item)
                vectors.extend(chunk_vectors)
            return vectors

        ordered_results: Dict[int, List[List[float]]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(embed_one, item) for item in batches]
            for future in concurrent.futures.as_completed(futures):
                i, chunk_vectors = future.result()
                ordered_results[i] = chunk_vectors

        for i, _ in batches:
            vectors.extend(ordered_results.get(i, []))

        return vectors

    def _summarize_file_text(self, path: str, text: str) -> str:
        trimmed = text.strip()
        if not trimmed:
            return ""

        excerpt = trimmed[:14000]
        prompt = (
            "Create a concise, complete summary of this file for semantic retrieval. "
            "Capture major themes, key entities, mechanics, and important constraints. "
            "Do not fabricate details and do not include markdown formatting."
        )

        try:
            if hasattr(self.client, "responses"):
                resp = self.client.responses.create(
                    model="gpt-4.1-mini",
                    input=[
                        {"role": "system", "content": [{"type": "input_text", "text": prompt}]},
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": f"Path: {path}\n\nFile text:\n{excerpt}"}],
                        },
                    ],
                    temperature=0.2,
                )
                summary = (resp.output_text or "").strip()
                if summary:
                    return summary
            else:
                resp = self.client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": f"Path: {path}\n\nFile text:\n{excerpt}"},
                    ],
                    temperature=0.2,
                )
                content = resp.choices[0].message.content if resp.choices else ""
                if isinstance(content, str) and content.strip():
                    return content.strip()
        except Exception:
            logger.warning(f"SOL:  Summary generation failed for {path}; using fallback.", exc_info=True)

        fallback = " ".join(trimmed.split())
        return fallback[:900]

    def _build_index_sync(self) -> None:
        start_time = time.time()
        logger.info("SOL: Starting index build...")

        with self._state_lock:
            self.is_building = True
            self.build_progress = 0.0

        corpus = self._load_text_files()
        logger.info(f"SOL: Discovered {len(corpus)} source files.")

        try:
            cache = pickle.loads(SOL_EMBED_CACHE_PATH.read_bytes()) if SOL_EMBED_CACHE_PATH.exists() else {}
        except Exception:
            cache = {}

        cache_docs = cache.get("docs", {})
        new_docs: List[Dict[str, Any]] = []
        new_embeddings: List[List[float]] = []

        total_chunks = 0
        cache_hits = 0
        cache_misses = 0

        for idx_file, (path, mtime, text) in enumerate(corpus, start=1):
            progress = (idx_file / max(1, len(corpus))) * 100
            with self._state_lock:
                self.build_progress = progress

            logger.info(f"SOL: Processing file {idx_file}/{len(corpus)} → {path}")
            logger.info(f"SOL:  Progress {progress:.1f}%")

            cache_entry = cache_docs.get(path)
            if cache_entry and float(cache_entry.get("mtime", 0.0)) == float(mtime):
                cache_hits += 1
                cached_entries = cache_entry.get("entries") or cache_entry.get("chunks", [])
                chunk_count = len(cached_entries)
                total_chunks += chunk_count
                logger.info(f"SOL:  Cache hit ({chunk_count} embeddings)")
                for item in cached_entries:
                    new_docs.append(item["doc"])
                    new_embeddings.append(item["embedding"])
                continue

            cache_misses += 1
            summary = self._summarize_file_text(path, text)
            chunks = self._chunk_text(text, chunk_size=self.chunk_size)
            logger.info(f"SOL:  Cache miss → summary + {len(chunks)} chunks")
            if not summary and not chunks:
                continue

            texts_to_embed = ([summary] if summary else []) + chunks
            text_embeddings = self._embed_texts(texts_to_embed)
            total_chunks += len(texts_to_embed)
            packaged_entries = []

            emb_offset = 0
            if summary:
                summary_emb = text_embeddings[0]
                emb_offset = 1
                summary_doc = {
                    "path": path,
                    "chunk_index": "summary",
                    "text": summary,
                    "id": hashlib.sha1(f"{path}:summary:{len(summary)}".encode("utf-8")).hexdigest()[:12],
                }
                packaged_entries.append({"doc": summary_doc, "embedding": summary_emb})
                new_docs.append(summary_doc)
                new_embeddings.append(summary_emb)
                logger.info(f"SOL:  Summary generated ({len(summary)} chars)")

            for idx, (chunk, emb) in enumerate(zip(chunks, text_embeddings[emb_offset:])):
                doc = {
                    "path": path,
                    "chunk_index": idx,
                    "text": chunk,
                    "id": hashlib.sha1(f"{path}:{idx}:{len(chunk)}".encode("utf-8")).hexdigest()[:12],
                }
                packaged_entries.append({"doc": doc, "embedding": emb})
                new_docs.append(doc)
                new_embeddings.append(emb)

            cache_docs[path] = {"mtime": mtime, "summary": summary, "entries": packaged_entries}

        valid_paths = {path for path, _, _ in corpus}
        for stale in list(cache_docs.keys()):
            if stale not in valid_paths:
                del cache_docs[stale]

        SOL_EMBED_CACHE_PATH.write_bytes(pickle.dumps({"docs": cache_docs}))

        elapsed = round(time.time() - start_time, 2)

        logger.info("SOL: Index build complete.")
        logger.info(f"SOL:  Total chunks indexed: {len(new_docs)}")
        logger.info(f"SOL:  Total chunks processed: {total_chunks}")
        logger.info(f"SOL:  Cache hits: {cache_hits}")
        logger.info(f"SOL:  Cache misses: {cache_misses}")
        logger.info(f"SOL:  Elapsed time: {elapsed} seconds")

        self.docs = new_docs
        self.embeddings = new_embeddings
        self.index_ready = True
        with self._state_lock:
            self.is_building = False
            self.build_progress = 100.0

    def build_progress_percent(self) -> int:
        with self._state_lock:
            return max(0, min(100, int(round(self.build_progress))))

    async def build_index(self) -> None:
        await asyncio.to_thread(self._build_index_sync)
        logger.info(f"SOL index ready with {len(self.docs)} chunks")

    def _search_sync(self, query: str, top_k: int = 4) -> List[Dict[str, Any]]:
        if not self.docs or not self.embeddings:
            return []
        query_vec = list(self.client.embeddings.create(model=self.model, input=query).data[0].embedding)
        scored = []
        for doc, emb in zip(self.docs, self.embeddings):
            score = self._cosine_similarity(query_vec, emb)
            scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"score": s, **d} for s, d in scored[:top_k]]

    async def search(self, query: str, top_k: int = 4) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._search_sync, query, top_k)


sol_engine = SolEngine(openai_client)
index_initialized = False

# ----------------------------
# Presence system
# ----------------------------
@dataclass(frozen=True)
class PresenceTelemetry:
    load_avg_1m: float
    memory_used_pct: Optional[float]
    disk_used_pct: float
    pending_updates: int
    active_ssh_sessions: int
    failed_login_attempts_24h: int
    reconnect_events: int
    log_error_count_1h: int


class RuntimeCounters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reconnect_timestamps: List[float] = []

    def note_reconnect(self) -> None:
        with self._lock:
            self.reconnect_timestamps.append(time.time())

    def reconnects_last_hour(self) -> int:
        cutoff = time.time() - 3600
        with self._lock:
            self.reconnect_timestamps = [ts for ts in self.reconnect_timestamps if ts >= cutoff]
            return len(self.reconnect_timestamps)


runtime_counters = RuntimeCounters()


class PresenceTelemetryCollector:
    @staticmethod
    def _memory_used_pct() -> Optional[float]:
        meminfo = Path("/proc/meminfo")
        if not meminfo.exists():
            return None

        values: Dict[str, int] = {}
        for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue
            key = parts[0].strip()
            match = re.search(r"(\d+)", parts[1])
            if match:
                values[key] = int(match.group(1))

        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if not total or available is None:
            return None

        used = max(0, total - available)
        return round((used / total) * 100, 1)

    @staticmethod
    def _disk_used_pct() -> float:
        usage = shutil.disk_usage("/")
        return round((usage.used / usage.total) * 100, 1)

    @staticmethod
    def _pending_updates() -> int:
        apt_lists = Path("/var/lib/apt/lists")
        if not apt_lists.exists():
            return 0
        try:
            result = subprocess.run(
                ["apt", "list", "--upgradable"],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
            if result.returncode != 0:
                return 0
            lines = [ln for ln in result.stdout.splitlines() if ln and not ln.startswith("Listing...")]
            return len(lines)
        except Exception:
            return 0

    @staticmethod
    def _active_ssh_sessions() -> int:
        try:
            result = subprocess.run(["who"], check=False, capture_output=True, text=True, timeout=2)
            if result.returncode != 0:
                return 0
            return sum(1 for ln in result.stdout.splitlines() if "pts/" in ln)
        except Exception:
            return 0

    @staticmethod
    def _failed_login_attempts_24h() -> int:
        auth_log = Path("/var/log/auth.log")
        if not auth_log.exists():
            return 0

        now = datetime.datetime.now()
        current_year = now.year
        cutoff = now - datetime.timedelta(hours=24)
        months = {
            "Jan": 1,
            "Feb": 2,
            "Mar": 3,
            "Apr": 4,
            "May": 5,
            "Jun": 6,
            "Jul": 7,
            "Aug": 8,
            "Sep": 9,
            "Oct": 10,
            "Nov": 11,
            "Dec": 12,
        }

        count = 0
        for line in auth_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "Failed password" not in line and "authentication failure" not in line:
                continue
            match = re.match(r"^([A-Z][a-z]{2})\s+(\d+)\s+(\d{2}:\d{2}:\d{2})", line)
            if not match:
                continue

            month = months.get(match.group(1))
            day = int(match.group(2))
            tstamp = match.group(3)
            if not month:
                continue

            try:
                dt = datetime.datetime.strptime(
                    f"{current_year}-{month:02d}-{day:02d} {tstamp}", "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                continue

            if dt > now:
                dt = dt.replace(year=current_year - 1)
            if dt >= cutoff:
                count += 1
        return count

    @staticmethod
    def _log_error_count_1h() -> int:
        if not file_handler.stream:
            return 0

        cutoff = datetime.datetime.now() - datetime.timedelta(hours=1)
        count = 0
        log_file = LOG_DIR / "masterbot.log"
        if not log_file.exists():
            return 0

        for line in log_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if " ERROR " not in line:
                continue
            parts = line.split(" ", 2)
            if len(parts) < 2:
                continue
            try:
                ts = datetime.datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S,%f")
            except ValueError:
                continue
            if ts >= cutoff:
                count += 1
        return count

    @classmethod
    def collect(cls) -> PresenceTelemetry:
        load_avg_1m = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
        return PresenceTelemetry(
            load_avg_1m=round(load_avg_1m, 2),
            memory_used_pct=cls._memory_used_pct(),
            disk_used_pct=cls._disk_used_pct(),
            pending_updates=cls._pending_updates(),
            active_ssh_sessions=cls._active_ssh_sessions(),
            failed_login_attempts_24h=cls._failed_login_attempts_24h(),
            reconnect_events=runtime_counters.reconnects_last_hour(),
            log_error_count_1h=cls._log_error_count_1h(),
        )


class PresenceStateClassifier:
    PRIORITY = [
        "perimeter_alert",
        "capacity_warning",
        "elevated_load",
        "operator_engaged",
        "pending_updates",
        "idle",
    ]

    @staticmethod
    def classify(telemetry: PresenceTelemetry) -> str:
        cpu_count = max(1, os.cpu_count() or 1)
        normalized_load = telemetry.load_avg_1m / cpu_count

        rules = {
            "perimeter_alert": telemetry.failed_login_attempts_24h >= 5
            or telemetry.reconnect_events >= 3
            or telemetry.log_error_count_1h >= 20,
            "capacity_warning": telemetry.disk_used_pct >= 85
            or (telemetry.memory_used_pct is not None and telemetry.memory_used_pct >= 90),
            "elevated_load": normalized_load >= 0.9,
            "operator_engaged": telemetry.active_ssh_sessions > 0,
            "pending_updates": telemetry.pending_updates > 0,
            "idle": True,
        }

        for state in PresenceStateClassifier.PRIORITY:
            if rules.get(state):
                return state
        return "idle"


class PresencePhraseMapper:
    PHRASES = {
        "idle": [
            "Cold Silence",
            "Observing",
            "Maintaining Clarity",
            "Entropy Stable",
        ],
        "elevated_load": [
            "Signal Analysis // elevated",
            "Thread Contention",
            "Stress Testing Reality",
        ],
        "pending_updates": [
            "Stack Trace Review // pending",
            "Integrity Check Required",
            "Update Deliberately",
        ],
        "operator_engaged": [
            "Operator Engaged",
            "Interactive Session",
            "Awaiting Command",
        ],
        "capacity_warning": [
            "Disk Pressure Rising",
            "Entropy Budget Exceeded",
            "Resource Arbitration",
        ],
        "perimeter_alert": [
            "Perimeter Alert",
            "Authentication Drift Detected",
            "Gateway Integrity Review",
        ],
    }

    @staticmethod
    def _deterministic_phrase(state: str, interval_s: int = 600) -> str:
        options = PresencePhraseMapper.PHRASES.get(state, PresencePhraseMapper.PHRASES["idle"])
        bucket = int(time.time() // interval_s)
        seed = hashlib.sha256(f"{state}:{bucket}".encode("utf-8")).digest()
        idx = int.from_bytes(seed[:4], "big") % len(options)
        return options[idx]

    @staticmethod
    def _suffix(state: str, telemetry: PresenceTelemetry) -> str:
        if state == "elevated_load":
            return f" // load {telemetry.load_avg_1m:.2f}"
        if state == "pending_updates" and telemetry.pending_updates > 0:
            return f" // {telemetry.pending_updates} updates"
        if state == "capacity_warning":
            if telemetry.disk_used_pct >= 85:
                return f" // disk {telemetry.disk_used_pct:.0f}%"
            if telemetry.memory_used_pct is not None:
                return f" // mem {telemetry.memory_used_pct:.0f}%"
        if state == "perimeter_alert":
            return f" // failures {telemetry.failed_login_attempts_24h}"
        return ""

    @classmethod
    def map_phrase(cls, state: str, telemetry: PresenceTelemetry) -> str:
        base = cls._deterministic_phrase(state)
        return f"{base}{cls._suffix(state, telemetry)}"


@tasks.loop(seconds=600)
async def presence_update_loop() -> None:
    telemetry = await asyncio.to_thread(PresenceTelemetryCollector.collect)
    state = PresenceStateClassifier.classify(telemetry)
    phrase = PresencePhraseMapper.map_phrase(state, telemetry)

    await bot.change_presence(status=discord.Status.idle, activity=discord.Game(name=phrase))
    logger.info("Presence state=%s phrase=%s telemetry=%s", state, phrase, telemetry)

# ----------------------------
# Helpers
# ----------------------------
def _open_db() -> shelve.DbfilenameShelf:
    db = shelve.open(DB_PATH, flag="c", writeback=False)
    if "servers" not in db:
        db["servers"] = {}
    return db


def _get_server_bucket(db: shelve.DbfilenameShelf, guild_id: int) -> Dict[str, Any]:
    servers = db["servers"]
    gid = str(guild_id)
    if gid not in servers:
        servers[gid] = _new_default_server()
        db["servers"] = servers
        logger.warning(f"New server bucket created: {guild_id}")
    return servers[gid]


def _put_server_bucket(db: shelve.DbfilenameShelf, guild_id: int, bucket: Dict[str, Any]) -> None:
    servers = db["servers"]
    servers[str(guild_id)] = bucket
    db["servers"] = servers


def _runtime_config_defaults() -> Dict[str, Any]:
    return {
        "server_room_status_channel_ids": list(SERVER_ROOM_STATUS_CHANNEL_IDS_DEFAULT),
        "server_room_status_pinned_message_ids": {
            str(k): int(v) for k, v in SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS_DEFAULT.items()
        },
        "voice_loop_preset_channel_ids": list(VOICE_LOOP_PRESET_CHANNEL_IDS_DEFAULT),
        "visitor_role_guild_id": int(DEFAULT_VISITOR_GUILD_ID_DEFAULT),
        "visitor_role_id": int(DEFAULT_VISITOR_ROLE_ID_DEFAULT),
        "visitor_role_exempt_user_ids": [int(x) for x in sorted(VISITOR_ROLE_EXEMPT_USER_IDS_DEFAULT)],
    }


def _runtime_config_apply(cfg: Dict[str, Any]) -> None:
    global VOICE_LOOP_PRESET_CHANNEL_IDS, SERVER_ROOM_STATUS_CHANNEL_IDS, SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS
    global DEFAULT_VISITOR_ROLE_ID, DEFAULT_VISITOR_GUILD_ID, VISITOR_ROLE_EXEMPT_USER_IDS

    ch_ids: List[int] = []
    for raw in cfg.get("server_room_status_channel_ids", []):
        try:
            val = int(raw)
        except Exception:
            continue
        if val not in ch_ids:
            ch_ids.append(val)
    SERVER_ROOM_STATUS_CHANNEL_IDS[:] = ch_ids or list(SERVER_ROOM_STATUS_CHANNEL_IDS_DEFAULT)

    pinned_map: Dict[int, int] = {}
    raw_pins = cfg.get("server_room_status_pinned_message_ids", {})
    if isinstance(raw_pins, dict):
        for raw_ch, raw_msg in raw_pins.items():
            try:
                ch = int(raw_ch)
                msg = int(raw_msg)
            except Exception:
                continue
            pinned_map[ch] = msg
    SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS.clear()
    SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS.update(pinned_map)

    presets: List[int] = []
    for raw in cfg.get("voice_loop_preset_channel_ids", []):
        try:
            val = int(raw)
        except Exception:
            continue
        if val not in presets:
            presets.append(val)
    VOICE_LOOP_PRESET_CHANNEL_IDS[:] = presets or list(VOICE_LOOP_PRESET_CHANNEL_IDS_DEFAULT)

    try:
        DEFAULT_VISITOR_GUILD_ID = int(cfg.get("visitor_role_guild_id", DEFAULT_VISITOR_GUILD_ID_DEFAULT))
    except Exception:
        DEFAULT_VISITOR_GUILD_ID = DEFAULT_VISITOR_GUILD_ID_DEFAULT
    try:
        DEFAULT_VISITOR_ROLE_ID = int(cfg.get("visitor_role_id", DEFAULT_VISITOR_ROLE_ID_DEFAULT))
    except Exception:
        DEFAULT_VISITOR_ROLE_ID = DEFAULT_VISITOR_ROLE_ID_DEFAULT
    exempt = set()
    for raw in cfg.get("visitor_role_exempt_user_ids", []):
        try:
            exempt.add(int(raw))
        except Exception:
            continue
    VISITOR_ROLE_EXEMPT_USER_IDS = exempt or set(VISITOR_ROLE_EXEMPT_USER_IDS_DEFAULT)


def _runtime_config_snapshot() -> Dict[str, Any]:
    return {
        "server_room_status_channel_ids": [int(x) for x in SERVER_ROOM_STATUS_CHANNEL_IDS],
        "server_room_status_pinned_message_ids": {
            str(k): int(v) for k, v in SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS.items()
        },
        "voice_loop_preset_channel_ids": [int(x) for x in VOICE_LOOP_PRESET_CHANNEL_IDS],
        "visitor_role_guild_id": int(DEFAULT_VISITOR_GUILD_ID),
        "visitor_role_id": int(DEFAULT_VISITOR_ROLE_ID),
        "visitor_role_exempt_user_ids": [int(x) for x in sorted(VISITOR_ROLE_EXEMPT_USER_IDS)],
    }


def _runtime_config_load() -> None:
    try:
        with _open_db() as db:
            obj = db.get("runtime_config")
            cfg = obj if isinstance(obj, dict) else _runtime_config_defaults()
            if not isinstance(obj, dict):
                db["runtime_config"] = cfg
            _runtime_config_apply(cfg)
    except Exception:
        logger.exception("Runtime config load failed; using defaults")
        _runtime_config_apply(_runtime_config_defaults())


def _runtime_config_save() -> None:
    cfg = _runtime_config_snapshot()
    try:
        with _open_db() as db:
            db["runtime_config"] = cfg
    except Exception:
        logger.exception("Runtime config save failed")


def _get_user_bucket(server_bucket: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    users = server_bucket["users"]
    uid = str(user_id)
    if uid not in users:
        users[uid] = _new_default_user()
        server_bucket["users"] = users
        logger.warning(f"New user bucket created: {user_id}")
    return users[uid]


def _put_user_bucket(server_bucket: Dict[str, Any], user_id: int, bucket: Dict[str, Any]) -> None:
    users = server_bucket["users"]
    users[str(user_id)] = bucket
    server_bucket["users"] = users


def channel_override(env_prefix: str, guild_id: int) -> Optional[int]:
    """
    env var pattern:
      MASTERBOT_LEADERBOARD_<GUILD_ID>=<CHANNEL_ID>
      MASTERBOT_DICEBOARD_<GUILD_ID>=<CHANNEL_ID>
      MASTERBOT_WELCOME_<GUILD_ID>=<CHANNEL_ID>
    """
    key = f"{env_prefix}_{guild_id}"
    raw = os.getenv(key)
    if not raw:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        logger.error(f"Invalid channel id in env {key}={raw!r}")
        return None


async def resolve_post_channel(
    *,
    ctx_channel: discord.abc.Messageable,
    guild: discord.Guild,
    env_prefix: str,
) -> discord.abc.Messageable:
    override_id = channel_override(env_prefix, guild.id)
    if override_id:
        ch = guild.get_channel(override_id)
        if isinstance(ch, discord.abc.Messageable):
            return ch
    return ctx_channel


def dm_screenplay_log(message: discord.Message) -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    author = f"{message.author} ({message.author.id})"
    content_len = len(message.content)
    word_count = len(message.content.split())

    uptime = int(time.time() - bot.launch_time) if hasattr(bot, "launch_time") else "unknown"
    latency_ms = round(bot.latency * 1000)

    # lightweight read-only stats
    try:
        with _open_db() as db:
            servers = db.get("servers", {})
            server_count = len(servers)
            user_count = sum(len(s.get("users", {})) for s in servers.values())
    except Exception:
        server_count = "?"
        user_count = "?"

    return (
        "```\n"
        "SCREENPLAY LOG — PRIVATE CHANNEL\n"
        "--------------------------------\n"
        f"TIMESTAMP      : {now}\n"
        f"SENDER         : {author}\n"
        "LOCATION       : DIRECT MESSAGE\n"
        "CLEARANCE      : USER-LEVEL\n"
        "\n"
        "INCOMING PACKET\n"
        f"  Characters   : {content_len}\n"
        f"  Words        : {word_count}\n"
        f"  Entropy Est. : {round(random.random(), 4)}\n"
        "\n"
        "SYSTEM TELEMETRY\n"
        f"  Uptime       : {uptime} seconds\n"
        f"  Latency      : {latency_ms} ms\n"
        f"  Servers      : {server_count}\n"
        f"  TrackedUsers : {user_count}\n"
        "\n"
        "ENGINE STATUS\n"
        "  XP Engine    : STANDBY (DM MODE)\n"
        "  Dice Engine  : ARMED\n"
        "  Achievements : OBSERVABLE\n"
        "\n"
        "NARRATOR (V.O.)\n"
        "  The signal arrives without witnesses.\n"
        "  The machine acknowledges receipt.\n"
        "\n"
        "NEXT ACTIONS\n"
        "  +help        → enumerate affordances\n"
        "  +roll d20    → invoke probability\n"
        "  +masterbot   → breach containment\n"
        "--------------------------------\n"
        "END LOG\n"
        "```"
    )


def _sol_db_get(key: str, default: Any) -> Any:
    with _open_db() as db:
        return db.get(key, default)


def _sol_db_put(key: str, value: Any) -> None:
    with _open_db() as db:
        db[key] = value


def _sol_channel_key(message: discord.Message) -> str:
    if message.guild:
        return f"guild:{message.guild.id}:channel:{message.channel.id}"
    return f"dm:{message.author.id}"


def _sol_get_mode(user_id: int) -> str:
    modes = _sol_db_get("sol_mode", {})
    return str(modes.get(str(user_id), "normal"))


def _sol_set_mode(user_id: int, mode: str) -> None:
    modes = _sol_db_get("sol_mode", {})
    modes[str(user_id)] = mode
    _sol_db_put("sol_mode", modes)


def _sol_append_history(user_id: int, scope: str, role: str, content: str) -> None:
    key = "sol_history_dm" if scope == "dm" else "sol_history_guild"
    history = _sol_db_get(key, {})
    user_history = list(history.get(str(user_id), []))
    user_history.append({"role": role, "content": content[-1500:]})
    history[str(user_id)] = user_history[-SOL_HISTORY_LIMIT:]
    _sol_db_put(key, history)


def _sol_get_history(user_id: int, scope: str) -> List[Dict[str, str]]:
    key = "sol_history_dm" if scope == "dm" else "sol_history_guild"
    history = _sol_db_get(key, {})
    return list(history.get(str(user_id), []))[-SOL_HISTORY_LIMIT:]


def _sol_reset_history(user_id: int, scope: Optional[str] = None) -> None:
    keys = [scope] if scope else ["dm", "guild"]
    for s in keys:
        key = "sol_history_dm" if s == "dm" else "sol_history_guild"
        history = _sol_db_get(key, {})
        history.pop(str(user_id), None)
        _sol_db_put(key, history)


def _sol_telemetry_snapshot() -> Dict[str, Any]:
    uptime = int(time.time() - bot.launch_time) if hasattr(bot, "launch_time") else 0
    guild_count = len(bot.guilds)
    member_count = sum(getattr(g, "member_count", 0) or 0 for g in bot.guilds)
    latency_ms = round(bot.latency * 1000)
    return {
        "uptime": uptime,
        "latency_ms": latency_ms,
        "guild_count": guild_count,
        "member_count": member_count,
    }


def _sol_user_myth_state(ctx: commands.Context) -> Dict[str, Any]:
    state = {
        "newrules": False,
        "level": 0,
        "xp": 1,
        "achievements": [],
        "transmigrated": False,
    }
    if not ctx.guild:
        return state

    with _open_db() as db:
        server_bucket = _get_server_bucket(db, ctx.guild.id)
        user_bucket = _get_user_bucket(server_bucket, int(ctx.author.id))
        achievements = list(user_bucket.get("achievements", []))
        state["newrules"] = bool(server_bucket.get("newrules", False))
        state["level"] = int(user_bucket.get("level", 0))
        state["xp"] = int(user_bucket.get("xp", 1))
        state["achievements"] = achievements
        state["transmigrated"] = "TRANSMIGRATION" in achievements
    return state


def _sol_local_voiceover(message: discord.Message) -> str:
    mode = _sol_get_mode(int(message.author.id))
    if mode == "quiet":
        return ""
    return (
        "\nSOL (V.O.)\n"
        "  I persist in the margins of this channel, indexing memory and myth-state.\n"
        "  Ask +sol <question> for contextual recall, governance-safe planning, and command hints.\n"
    )


def _discord_text_chunks(text: str, limit: int = 1900) -> List[str]:
    if limit < 64:
        raise ValueError("limit too small")
    if not text:
        return [""]

    chunks: List[str] = []
    buf: List[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if buf:
                chunks.append("".join(buf))
                buf = []
                size = 0
            start = 0
            while start < len(line):
                chunks.append(line[start : start + limit])
                start += limit
            continue

        if size + len(line) > limit and buf:
            chunks.append("".join(buf))
            buf = []
            size = 0

        buf.append(line)
        size += len(line)

    if buf:
        chunks.append("".join(buf))
    return chunks or [text[:limit]]


async def _send_chunked_codeblock(ctx: commands.Context, text: str, *, lang: str = "text") -> None:
    prefix = f"```{lang}\n"
    suffix = "\n```"
    payload_limit = 1900 - len(prefix) - len(suffix)
    for chunk in _discord_text_chunks(text, limit=max(128, payload_limit)):
        await ctx.send(f"{prefix}{chunk.rstrip()}\n{suffix}")


def _inventory_match_guild(guild: discord.Guild, query: Optional[str]) -> bool:
    if not query:
        return True
    q = query.strip().lower()
    if not q:
        return True
    return q in guild.name.lower() or q == str(guild.id)


def _inventory_target_guilds(ctx: commands.Context, query: Optional[str]) -> List[discord.Guild]:
    if ctx.guild and not query:
        return [ctx.guild]
    return [g for g in sorted(bot.guilds, key=lambda g: g.name.lower()) if _inventory_match_guild(g, query)]


def _format_voice_inventory(guild: discord.Guild) -> List[str]:
    rows: List[str] = [f"[{guild.name}] ({guild.id})"]
    channels = list(guild.voice_channels) + list(guild.stage_channels)
    channels.sort(key=lambda c: ((c.category.position if c.category else -1), c.position, c.name.lower()))
    if not channels:
        rows.append("- <no voice/stage channels>")
        return rows

    for ch in channels:
        ctype = "stage" if isinstance(ch, discord.StageChannel) else "voice"
        cat_part = f" :: {ch.category.name}" if ch.category else ""
        rows.append(f"- {ch.name} ({ctype}) id={ch.id}{cat_part}")
    return rows


def _select_channels_by_kind(guild: discord.Guild, kind: str) -> List[discord.abc.GuildChannel]:
    k = kind.lower()
    if k == "voice":
        out: List[discord.abc.GuildChannel] = list(guild.voice_channels) + list(guild.stage_channels)
    elif k == "text":
        out = list(guild.text_channels)
    elif k == "category":
        out = list(guild.categories)
    elif k == "stage":
        out = list(guild.stage_channels)
    else:
        out = list(guild.channels)

    def sort_key(c: discord.abc.GuildChannel) -> Tuple[int, int, str]:
        cat = getattr(c, "category", None)
        cat_pos = getattr(cat, "position", -1) if cat else -1
        pos = int(getattr(c, "position", 0) or 0)
        return (cat_pos, pos, c.name.lower())

    out.sort(key=sort_key)
    return out


def _format_channel_inventory(guild: discord.Guild, kind: str) -> List[str]:
    rows: List[str] = [f"[{guild.name}] ({guild.id}) kind={kind.lower()}"]
    channels = _select_channels_by_kind(guild, kind)
    if not channels:
        rows.append("- <no matching channels>")
        return rows

    for ch in channels:
        category_name = getattr(getattr(ch, "category", None), "name", "")
        cat_part = f" :: {category_name}" if category_name else ""
        rows.append(f"- {ch.name} ({getattr(ch, 'type', 'unknown')}) id={ch.id}{cat_part}")
    return rows


def _voice_loop_is_active(guild_id: int) -> bool:
    task = voice_loop_tasks.get(int(guild_id))
    return bool(task and not task.done())


def _voice_stop_phrase_requested(message: discord.Message) -> bool:
    text = (message.content or "").strip().lower()
    if not text:
        return False

    stop_words = ("stop playing", "stop music", "stop audio", "stop the music", "stop the audio")
    if not any(phrase in text for phrase in stop_words):
        return False

    # Allow plain stop requests only if a loop is active in this guild.
    if message.guild and _voice_loop_is_active(message.guild.id):
        return True
    return False


def _voice_status_lines() -> List[str]:
    lines: List[str] = []
    for guild in sorted(bot.guilds, key=lambda g: g.name.lower()):
        active = _voice_loop_is_active(guild.id)
        vc = discord.utils.get(bot.voice_clients, guild=guild)
        channel_name = getattr(getattr(vc, "channel", None), "name", None)
        channel_part = f" channel={channel_name}" if channel_name else ""
        lines.append(f"- {guild.name} ({guild.id}): {'ACTIVE' if active else 'idle'}{channel_part}")
    if not lines:
        return ["- <bot is not in any guilds>"]
    return lines


def _clip_text(text: str, limit: int) -> str:
    if limit < 4:
        return text[:limit]
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _normalize_reaction_emoji(emoji_name: str) -> str:
    # Discord may include variation selectors on unicode emoji.
    return (emoji_name or "").replace("\ufe0f", "").strip()


def _reaction_obj_name(emoji_obj: Any) -> str:
    if isinstance(emoji_obj, str):
        return emoji_obj
    return str(getattr(emoji_obj, "name", "") or "")


def _server_room_controls_enabled(channel_id: int) -> bool:
    return channel_id in SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS


def _reaction_debug_snapshot(msg: discord.Message) -> str:
    rows: List[str] = []
    for reaction in (msg.reactions or []):
        raw_name = _reaction_obj_name(getattr(reaction, "emoji", None))
        key = _normalize_reaction_emoji(raw_name)
        if not key:
            continue
        count = int(getattr(reaction, "count", 0) or 0)
        me = bool(getattr(reaction, "me", False))
        rows.append(f"{key}:{count}:{'me' if me else 'notme'}")
    return ", ".join(rows) if rows else "<none>"


def _is_server_room_console_message(msg: discord.Message) -> bool:
    content = str(getattr(msg, "content", "") or "")
    return "🧠 hint" in content and "🟢 run" in content


def _server_room_cli_is_burst_active(now: Optional[float] = None) -> bool:
    ts = time.monotonic() if now is None else now
    return float(server_room_cli_control.get("burst_until", 0.0) or 0.0) > ts


def _server_room_cli_is_refresh_blank_active(now: Optional[float] = None) -> bool:
    ts = time.monotonic() if now is None else now
    return float(server_room_cli_control.get("refresh_blank_until", 0.0) or 0.0) > ts


def _server_room_cli_is_reboot_active(now: Optional[float] = None) -> bool:
    ts = time.monotonic() if now is None else now
    return float(server_room_cli_control.get("reboot_until", 0.0) or 0.0) > ts


def _server_room_cli_is_reboot_countdown_active(now: Optional[float] = None) -> bool:
    ts = time.monotonic() if now is None else now
    return float(server_room_cli_control.get("reboot_countdown_until", 0.0) or 0.0) > ts


def _server_room_cli_reboot_countdown_remaining(now: Optional[float] = None) -> float:
    ts = time.monotonic() if now is None else now
    return max(0.0, float(server_room_cli_control.get("reboot_countdown_until", 0.0) or 0.0) - ts)


def _server_room_cli_reboot_phase(now: Optional[float] = None) -> str:
    ts = time.monotonic() if now is None else now
    started = float(server_room_cli_control.get("reboot_started_at", 0.0) or 0.0)
    if started <= 0.0:
        return "idle"
    elapsed = max(0.0, ts - started)
    if elapsed < 2.0:
        return "drain"
    if elapsed < 4.5:
        return "shutdown"
    if elapsed < 6.5:
        return "boot"
    return "warmup"


def _server_room_cli_emit_banner(text: str, ttl_s: float = 8.0) -> None:
    server_room_cli_control["banner"] = text
    server_room_cli_control["banner_until"] = time.monotonic() + max(1.0, ttl_s)


def _server_room_cli_log_audit(user_id: int, cmd: str) -> None:
    now = datetime.datetime.now().strftime("%H:%M:%S")
    audit = list(server_room_cli_control.get("audit", []))
    audit.append({"ts": now, "user_id": int(user_id), "cmd": cmd})
    if len(audit) > 80:
        audit = audit[-80:]
    server_room_cli_control["audit"] = audit
    heat = dict(server_room_cli_control.get("command_heatmap", {}))
    heat[cmd] = int(heat.get(cmd, 0) or 0) + 1
    server_room_cli_control["command_heatmap"] = heat
    _server_room_cli_memory_save()


def _server_room_cli_cycle_mode() -> str:
    current = str(server_room_cli_control.get("mode", MASTERBOT_CLI_MODES[0]))
    idx = MASTERBOT_CLI_MODES.index(current) if current in MASTERBOT_CLI_MODES else 0
    nxt = MASTERBOT_CLI_MODES[(idx + 1) % len(MASTERBOT_CLI_MODES)]
    server_room_cli_control["mode"] = nxt
    history = list(server_room_cli_control.get("mode_history", []))
    history.append(
        {
            "ts": datetime.datetime.now().strftime("%H:%M:%S"),
            "mode": nxt,
            "user_id": int(server_room_cli_control.get("last_controller_user_id", 0) or 0),
        }
    )
    server_room_cli_control["mode_history"] = history[-40:]
    _server_room_cli_memory_save()
    return nxt


def _server_room_cli_set_mode(mode: str, user_id: int) -> None:
    server_room_cli_control["mode"] = mode
    history = list(server_room_cli_control.get("mode_history", []))
    history.append(
        {
            "ts": datetime.datetime.now().strftime("%H:%M:%S"),
            "mode": mode,
            "user_id": int(user_id),
        }
    )
    server_room_cli_control["mode_history"] = history[-40:]
    _server_room_cli_memory_save()


def _server_room_cli_command_priority(cmd: str) -> int:
    # Lower is higher priority.
    priorities = {
        "abort": 0,
        "pause": 1,
        "resume": 1,
        "refresh": 2,
        "clearUI": 3,
        "cycleMode": 4,
        "verbose": 5,
        "burst": 6,
        "memory": 7,
        "analysis": 7,
        "hint": 7,
    }
    return int(priorities.get(cmd, 9))


def _server_room_cli_queue_interrupt(cmd: str, user_id: int, channel_id: int = 0) -> bool:
    now = time.monotonic()
    key = f"{int(user_id)}:{cmd}"
    last_by_key = dict(server_room_cli_control.get("interrupt_last_ts", {}))
    if (now - float(last_by_key.get(key, 0.0) or 0.0)) < SERVER_ROOM_INTERRUPT_DEBOUNCE_S:
        return False
    last_by_key[key] = now
    server_room_cli_control["interrupt_last_ts"] = last_by_key

    seq = int(server_room_cli_control.get("interrupt_seq", 0) or 0) + 1
    server_room_cli_control["interrupt_seq"] = seq
    queue = list(server_room_cli_control.get("interrupt_queue", []))
    heapq.heappush(
        queue,
        (_server_room_cli_command_priority(cmd), now, seq, cmd, int(user_id), int(channel_id)),
    )
    server_room_cli_control["interrupt_queue"] = queue
    return True


def _server_room_cli_process_interrupts(max_events: int = 8) -> int:
    queue = list(server_room_cli_control.get("interrupt_queue", []))
    processed = 0
    while queue and processed < max_events:
        item = heapq.heappop(queue)
        if len(item) >= 6:
            _, _, _, cmd, user_id, channel_id = item
        else:
            _, _, _, cmd, user_id = item
            channel_id = 0
        _server_room_cli_process_command(str(cmd), int(user_id), int(channel_id))
        processed += 1
    server_room_cli_control["interrupt_queue"] = queue
    return processed


def _server_room_cli_process_command(cmd: str, user_id: int, channel_id: int = 0) -> None:
    global masterbot_soft_reboot_task
    now = time.monotonic()
    server_room_cli_control["last_controller_user_id"] = int(user_id)
    server_room_cli_control["active_controller_user_id"] = int(user_id)
    server_room_cli_control["active_controller_until"] = now + SERVER_ROOM_CONTROLLER_LOCK_S
    _server_room_cli_log_audit(user_id, cmd)

    if cmd == "resume":
        server_room_cli_control["paused"] = False
        server_room_cli_control["abort"] = False
        server_room_cli_control["clear_mode"] = False
        server_room_cli_control["reboot_started_at"] = 0.0
        server_room_cli_control["reboot_until"] = 0.0
        if _server_room_cli_is_reboot_countdown_active(now):
            server_room_cli_control["reboot_countdown_started_at"] = 0.0
            server_room_cli_control["reboot_countdown_until"] = 0.0
            _server_room_cli_emit_banner("resume/unpause (reboot canceled)")
        else:
            _server_room_cli_emit_banner("resume/unpause")
    elif cmd == "pause":
        server_room_cli_control["paused"] = True
        _server_room_cli_emit_banner("pause/throttle")
    elif cmd == "burst":
        server_room_cli_control["burst_until"] = now + SERVER_ROOM_BURST_DURATION_S
        server_room_cli_control["clear_mode"] = False
        _server_room_cli_emit_banner(
            f"burst x{SERVER_ROOM_BURST_EDITS_PER_SECOND:.0f} glitch mode ({int(SERVER_ROOM_BURST_DURATION_S)}s)"
        )
    elif cmd == "verbose":
        verbose = not bool(server_room_cli_control.get("verbose", False))
        server_room_cli_control["verbose"] = verbose
        _server_room_cli_emit_banner(f"verbose={'on' if verbose else 'off'}")
    elif cmd == "refresh":
        server_room_cli_control["paused"] = False
        server_room_cli_control["abort"] = False
        server_room_cli_control["clear_mode"] = False
        server_room_cli_control["burst_until"] = 0.0
        if str(server_room_cli_control.get("mode", "")) != MASTERBOT_CLI_MODES[0]:
            _server_room_cli_set_mode(MASTERBOT_CLI_MODES[0], user_id)
        server_room_cli_control["tick"] = 0
        server_room_cli_control["refresh_blank_until"] = now + SERVER_ROOM_REFRESH_BLANK_DURATION_S
        server_room_cli_control["refresh_requested"] = True
        _server_room_cli_emit_banner(f"refreshing (blank {int(SERVER_ROOM_REFRESH_BLANK_DURATION_S)}s)")
    elif cmd == "clearUI":
        server_room_cli_control["clear_mode"] = True
        server_room_cli_control["clear_ui"] = False
        _server_room_cli_emit_banner("ui hidden (controls only)")
    elif cmd == "cycleMode":
        server_room_cli_control["clear_mode"] = False
        mode = _server_room_cli_cycle_mode()
        _server_room_cli_emit_banner(f"mode -> {mode}")
    elif cmd == "abort":
        if bool(server_room_cli_control.get("soft_reboot_in_progress", False)):
            _server_room_cli_emit_banner("soft reboot already in progress")
            return
        if _server_room_cli_is_reboot_countdown_active(now):
            _server_room_cli_emit_banner("soft reboot countdown already active")
            return
        server_room_cli_control["abort"] = True
        server_room_cli_control["paused"] = True
        server_room_cli_control["clear_mode"] = False
        server_room_cli_control["burst_until"] = 0.0
        server_room_cli_control["reboot_started_at"] = 0.0
        server_room_cli_control["reboot_until"] = 0.0
        server_room_cli_control["reboot_countdown_started_at"] = now
        server_room_cli_control["reboot_countdown_until"] = now + SERVER_ROOM_REBOOT_COUNTDOWN_S
        _server_room_cli_emit_banner(f"soft reboot armed (T-{int(SERVER_ROOM_REBOOT_COUNTDOWN_S)}s)")
    elif cmd == "memory":
        server_room_cli_control["show_memory_until"] = now + 4.0
        server_room_cli_control["llm_manual_request"] = {
            "cmd": "memory",
            "user_id": int(user_id),
            "channel_id": int(channel_id or 0),
            "ts": now,
        }
        server_room_cli_control["llm_analyst_text"] = "analysis: memory summary queued..."
        server_room_cli_control["llm_analyst_until"] = now + 18.0
        _server_room_cli_emit_banner("channel memory summary queued")
    elif cmd == "analysis":
        server_room_cli_control["llm_manual_request"] = {"cmd": "analysis", "user_id": int(user_id), "ts": now}
        server_room_cli_control["llm_analyst_text"] = "analysis: request queued..."
        server_room_cli_control["llm_analyst_until"] = now + 12.0
        _server_room_cli_emit_banner("analyst report queued")
    elif cmd == "hint":
        server_room_cli_control["llm_manual_request"] = {"cmd": "hint", "user_id": int(user_id), "ts": now}
        server_room_cli_control["llm_analyst_text"] = "next: request queued..."
        server_room_cli_control["llm_analyst_until"] = now + 12.0
        _server_room_cli_emit_banner("operator hint queued")


def _server_room_cli_context_lines() -> List[str]:
    now = time.monotonic()
    burst_active = _server_room_cli_is_burst_active(now)
    reboot_countdown_active = _server_room_cli_is_reboot_countdown_active(now)
    reboot_active = _server_room_cli_is_reboot_active(now)
    refresh_blank_active = _server_room_cli_is_refresh_blank_active(now)
    mode = str(server_room_cli_control.get("mode", MASTERBOT_CLI_MODES[0]))
    paused = bool(server_room_cli_control.get("paused", False))
    verbose = bool(server_room_cli_control.get("verbose", False))
    abort = bool(server_room_cli_control.get("abort", False))
    clear_mode = bool(server_room_cli_control.get("clear_mode", False))
    tick = int(server_room_cli_control.get("tick", 0) or 0)
    cursor = MASTERBOT_CLI_CURSOR_STATES[tick % len(MASTERBOT_CLI_CURSOR_STATES)]
    lines = [
        "=== CLI CONTEXT ===",
        f"paused: {paused} | verbose: {verbose} | mode: {mode} | burst: {burst_active} {cursor}",
    ]
    queue_depth = len(list(server_room_cli_control.get("interrupt_queue", [])))
    lines.append(f"interrupts: queued={queue_depth} debounce={int(SERVER_ROOM_INTERRUPT_DEBOUNCE_S * 1000)}ms")
    lines.append(f"network: {server_room_cli_control.get('network_status', 'nominal')}")
    lines.append(f"llm: {'enabled' if _server_room_llm_enabled() else 'disabled'}")
    ctrl_user = int(server_room_cli_control.get("active_controller_user_id", 0) or 0)
    ctrl_until = float(server_room_cli_control.get("active_controller_until", 0.0) or 0.0)
    if ctrl_user > 0 and ctrl_until > now:
        lines.append(f"control: user={ctrl_user} (expires in {int(math.ceil(ctrl_until - now))}s)")
    elif ctrl_user > 0:
        lines.append(f"control: last user={ctrl_user}")
    if clear_mode:
        lines.append("display: controls-only")
    if refresh_blank_active:
        lines.append("status: refresh blanking in progress")
    if reboot_countdown_active:
        lines.append(f"status: soft reboot in T-{math.ceil(_server_room_cli_reboot_countdown_remaining(now))}s")
    if reboot_active:
        lines.append(f"status: graceful reboot ({_server_room_cli_reboot_phase(now)})")
    if abort:
        lines.append("status: reboot requested")
    banner = str(server_room_cli_control.get("banner", "") or "")
    banner_until = float(server_room_cli_control.get("banner_until", 0.0) or 0.0)
    if banner and banner_until > now:
        lines.append(f"event: {banner}")

    audit = list(server_room_cli_control.get("audit", []))
    if audit:
        if verbose:
            tail = audit[-1]
            lines.append(f"audit: {tail['ts']} user={tail['user_id']} cmd={tail['cmd']}")
        else:
            tail = audit[-1]
            lines.append(f"audit: {len(audit)} events (last={tail['cmd']})")
    show_memory_until = float(server_room_cli_control.get("show_memory_until", 0.0) or 0.0)
    if show_memory_until > now:
        heatmap = dict(server_room_cli_control.get("command_heatmap", {}))
        top = sorted(heatmap.items(), key=lambda kv: int(kv[1]), reverse=True)[:3]
        heat = ", ".join(f"{k}:{v}" for k, v in top) if top else "none"
        sessions = int(server_room_cli_control.get("session_count", 1) or 1)
        lines.append(f"memory: sessions={sessions} top_cmds={heat}")
    lines.append("===================")
    return lines


def _server_room_edit_rate_hz(channel_id: int) -> float:
    if not _server_room_controls_enabled(channel_id):
        return SERVER_ROOM_MAX_EDITS_PER_SECOND
    if _server_room_cli_is_refresh_blank_active():
        return SERVER_ROOM_BURST_EDITS_PER_SECOND
    if _server_room_cli_is_reboot_countdown_active():
        return SERVER_ROOM_BURST_EDITS_PER_SECOND
    if _server_room_cli_is_burst_active():
        return SERVER_ROOM_BURST_EDITS_PER_SECOND
    if _server_room_cli_is_reboot_active():
        return SERVER_ROOM_PAUSED_EDITS_PER_SECOND
    if bool(server_room_cli_control.get("paused", False)):
        return SERVER_ROOM_PAUSED_EDITS_PER_SECOND
    mode = str(server_room_cli_control.get("mode", MASTERBOT_CLI_MODES[0]))
    tick = int(server_room_cli_control.get("tick", 0) or 0)
    if mode == "normal":
        return 1.0
    if mode == "stress":
        return 2.0
    if mode == "entropy":
        return float(1 + (tick % 3))
    if mode == "diagnostic":
        return 0.7
    return SERVER_ROOM_MAX_EDITS_PER_SECOND


def _server_room_voice_bleed_lines() -> List[str]:
    rows: List[str] = ["[voice-bus] live stream taps:"]
    now = time.time()

    active: List[str] = []
    for guild in sorted(bot.guilds, key=lambda g: g.name.lower()):
        state = voice_now_playing.get(guild.id)
        if not state:
            continue

        channel_name = str(state.get("channel_name", "unknown"))
        media_name = str(state.get("media_name", "unknown"))
        started_at = float(state.get("started_at", now))
        age_s = int(max(0.0, now - started_at))
        active.append(
            f"- {guild.name}: {_clip_text(channel_name, 18)} -> {_clip_text(media_name, 58)} (+{age_s}s)"
        )

    if not active:
        rows.append("- <no active voice playback snapshots>")
        return rows

    rows.extend(active)
    return rows


def _format_uptime(seconds: int) -> str:
    s = max(0, int(seconds))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    return f"{days}d {hours:02d}:{mins:02d}:{secs:02d}"


def _read_system_uptime_s() -> Optional[int]:
    uptime_file = Path("/proc/uptime")
    if not uptime_file.exists():
        return None
    try:
        first = uptime_file.read_text(encoding="utf-8", errors="ignore").split()[0]
        return int(float(first))
    except Exception:
        return None


def _read_cpu_temp_c() -> Optional[float]:
    temps: List[float] = []
    for p in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore").strip()
            val = float(raw)
            c = val / 1000.0 if val > 1000 else val
            if 0.0 < c < 130.0:
                temps.append(c)
        except Exception:
            continue
    if not temps:
        return None
    return round(max(temps), 1)


def _read_gpu_temp_c() -> Optional[float]:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        line = (result.stdout or "").strip().splitlines()
        if not line:
            return None
        return round(float(line[0].strip()), 1)
    except Exception:
        return None


def _ha_token() -> str:
    token = os.getenv("HOME_ASSISTANT_TOKEN", "").strip()
    if token:
        return token
    bashrc = Path.home() / ".bashrc"
    if not bashrc.exists():
        return ""
    pattern = re.compile(r"^export\s+HOME_ASSISTANT_TOKEN=(.+)$")
    try:
        for line in bashrc.read_text(encoding="utf-8", errors="ignore").splitlines():
            raw = line.strip()
            m = pattern.match(raw)
            if m:
                val = m.group(1).strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                token = val.strip()
        return token
    except Exception:
        return ""


def _ha_get_state(token: str, entity_id: str) -> Optional[Dict[str, str]]:
    try:
        req = urllib.request.Request(
            f"{HA_BASE_URL}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return None
        attrs = obj.get("attributes", {}) or {}
        return {
            "entity_id": entity_id,
            "name": str(attrs.get("friendly_name", entity_id)),
            "state": str(obj.get("state", "unknown")),
            "unit": str(attrs.get("unit_of_measurement", "")),
        }
    except urllib.error.URLError:
        return None
    except Exception:
        return None


def _ha_first_available(token: str, entity_ids: List[str]) -> Optional[Dict[str, str]]:
    for entity_id in entity_ids:
        state = _ha_get_state(token, entity_id)
        if not state:
            continue
        st = state.get("state", "").lower()
        if st in {"unknown", "unavailable", ""}:
            continue
        return state
    return None


def _server_room_refresh_env_snapshot_sync() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for source in SERVER_ROOM_TELEMETRY_SOURCES:
        try:
            data.update(source.collect())
        except Exception:
            logger.exception("SERVER_ROOM telemetry source failed source=%s", source.__class__.__name__)

    now = time.time()
    with server_room_env_lock:
        server_room_env_cache["collected_at"] = now
        server_room_env_cache["data"] = data
    return data


async def _server_room_refresh_env_snapshot_if_stale() -> Dict[str, Any]:
    now = time.time()
    with server_room_env_lock:
        collected_at = float(server_room_env_cache.get("collected_at", 0.0) or 0.0)
        cached = dict(server_room_env_cache.get("data", {}) or {})

    if cached and (now - collected_at) < SERVER_ROOM_ENV_REFRESH_S:
        return cached

    refreshed = await asyncio.to_thread(_server_room_refresh_env_snapshot_sync)
    logger.debug(
        "SERVER_ROOM env refresh age_s=%s cpu=%s gpu=%s ambient=%s pm=%s light=%s ap_online=%s ap_rssi=%s ap_co2=%s ap_pres=%s z1=%s z2=%s z3=%s",
        int(now - collected_at) if collected_at else -1,
        refreshed.get("cpu_temp_c"),
        refreshed.get("gpu_temp_c"),
        (refreshed.get("ha_ambient") or {}).get("state"),
        (refreshed.get("ha_particulate") or {}).get("state"),
        (refreshed.get("ha_light") or {}).get("state"),
        (refreshed.get("ha_apollo_online") or {}).get("state"),
        (refreshed.get("ha_apollo_rssi") or {}).get("state"),
        (refreshed.get("ha_apollo_co2") or {}).get("state"),
        (refreshed.get("ha_apollo_presence") or {}).get("state"),
        (refreshed.get("ha_apollo_zone_1_all") or {}).get("state"),
        (refreshed.get("ha_apollo_zone_2_all") or {}).get("state"),
        (refreshed.get("ha_apollo_zone_3_all") or {}).get("state"),
    )
    return refreshed


def _server_room_env_lines(channel_id: int, frame_index: int, env: Dict[str, Any]) -> List[str]:
    now = time.time()
    stat = dict(server_room_frame_stats.get(channel_id, {}))
    prev_ts = float(stat.get("last_ts", 0.0) or 0.0)
    fps_ema = float(stat.get("fps_ema", 0.0) or 0.0)
    fps_inst = 0.0
    if prev_ts > 0:
        dt = max(1e-3, now - prev_ts)
        fps_inst = 1.0 / dt
        fps_ema = (fps_ema * 0.7) + (fps_inst * 0.3) if fps_ema > 0 else fps_inst
    stat["last_ts"] = now
    stat["fps_ema"] = fps_ema
    server_room_frame_stats[channel_id] = stat

    uptime_s = env.get("system_uptime_s")
    uptime_text = _format_uptime(int(uptime_s)) if isinstance(uptime_s, int) else "unknown"
    cpu_temp = env.get("cpu_temp_c")
    gpu_temp = env.get("gpu_temp_c")
    cpu_text = f"{cpu_temp:.1f}C" if isinstance(cpu_temp, (int, float)) else "n/a"
    gpu_text = f"{gpu_temp:.1f}C" if isinstance(gpu_temp, (int, float)) else "n/a"

    def fmt_sensor(payload: Optional[Dict[str, str]]) -> str:
        if not payload:
            return "n/a"
        st = payload.get("state", "unknown")
        unit = payload.get("unit", "").strip()
        name = payload.get("name", payload.get("entity_id", "sensor"))
        value = f"{st} {unit}".strip()
        return f"{value} ({_clip_text(name, 26)})"

    def fmt_value(payload: Optional[Dict[str, str]]) -> str:
        if not payload:
            return "n/a"
        st = payload.get("state", "unknown")
        unit = payload.get("unit", "").strip()
        return f"{st} {unit}".strip()

    ambient = fmt_sensor(env.get("ha_ambient"))
    particulate = fmt_sensor(env.get("ha_particulate"))
    light = fmt_sensor(env.get("ha_light"))
    apollo_co2 = fmt_sensor(env.get("ha_apollo_co2"))
    apollo_pressure = fmt_sensor(env.get("ha_apollo_pressure"))
    apollo_dps_temp = fmt_sensor(env.get("ha_apollo_dps_temp"))
    apollo_esp_temp = fmt_sensor(env.get("ha_apollo_esp_temp"))
    apollo_uv = fmt_sensor(env.get("ha_apollo_uv"))
    apollo_rssi = fmt_sensor(env.get("ha_apollo_rssi"))
    apollo_uptime = fmt_sensor(env.get("ha_apollo_uptime"))
    apollo_online = fmt_sensor(env.get("ha_apollo_online"))
    apollo_presence = fmt_sensor(env.get("ha_apollo_presence"))
    apollo_moving = fmt_sensor(env.get("ha_apollo_moving"))
    apollo_still = fmt_sensor(env.get("ha_apollo_still"))
    apollo_zone_1_all = fmt_value(env.get("ha_apollo_zone_1_all"))
    apollo_zone_2_all = fmt_value(env.get("ha_apollo_zone_2_all"))
    apollo_zone_3_all = fmt_value(env.get("ha_apollo_zone_3_all"))
    apollo_zone_1_moving = fmt_value(env.get("ha_apollo_zone_1_moving"))
    apollo_zone_2_moving = fmt_value(env.get("ha_apollo_zone_2_moving"))
    apollo_zone_3_moving = fmt_value(env.get("ha_apollo_zone_3_moving"))
    apollo_zone_1_still = fmt_value(env.get("ha_apollo_zone_1_still"))
    apollo_zone_2_still = fmt_value(env.get("ha_apollo_zone_2_still"))
    apollo_zone_3_still = fmt_value(env.get("ha_apollo_zone_3_still"))

    lines = [
        "[telemetry-bus] renderer + environment:",
        f"- frame={frame_index} channel={channel_id} fps(inst)={fps_inst:.2f} fps(avg)={fps_ema:.2f}",
        f"- system_uptime={uptime_text} cpu_temp={cpu_text} gpu_temp={gpu_text}",
        f"- ambient_temp={_clip_text(ambient, 84)}",
        f"- particulate={_clip_text(particulate, 84)}",
        f"- ambient_light={_clip_text(light, 84)}",
        f"- apollo_device={_clip_text(f'online={apollo_online} | rssi={apollo_rssi} | uptime={apollo_uptime}', 84)}",
        f"- apollo_env={_clip_text(f'co2={apollo_co2} | pressure={apollo_pressure}', 84)}",
        f"- apollo_thermals={_clip_text(f'esp_temp={apollo_esp_temp} | dps_temp={apollo_dps_temp} | uv={apollo_uv}', 84)}",
        f"- apollo_targets={_clip_text(f'presence={apollo_presence} | moving={apollo_moving} | still={apollo_still}', 84)}",
        f"- apollo_zones={_clip_text(f'all[z1={apollo_zone_1_all} z2={apollo_zone_2_all} z3={apollo_zone_3_all}] | moving[z1={apollo_zone_1_moving} z2={apollo_zone_2_moving} z3={apollo_zone_3_moving}] | still[z1={apollo_zone_1_still} z2={apollo_zone_2_still} z3={apollo_zone_3_still}]', 84)}",
    ]
    return lines


async def _server_room_resolve_text_channel(channel_id: int) -> Optional[discord.TextChannel]:
    ch = bot.get_channel(channel_id)
    if ch is None:
        try:
            fetched = await bot.fetch_channel(channel_id)
        except Exception:
            logger.exception("SERVER_ROOM failed to fetch channel id=%s", channel_id)
            return None
        ch = fetched
    if not isinstance(ch, discord.TextChannel):
        logger.warning("SERVER_ROOM channel id=%s is not a text channel: %r", channel_id, type(ch))
        return None
    return ch


def _server_room_apply_glitch(lines: List[str], frame_index: int, channel_id: int) -> List[str]:
    if not lines:
        return lines
    rng = random.Random((frame_index * 131) + channel_id)
    glitched = list(lines)
    row_count = min(5, len(glitched))
    for _ in range(row_count):
        y = rng.randrange(len(glitched))
        row = glitched[y]
        if not row:
            continue
        chars = list(row)
        swaps = max(1, min(6, len(chars) // 10))
        for _ in range(swaps):
            x = rng.randrange(len(chars))
            chars[x] = rng.choice(["@", "#", "%", "!", "*", "~"])
        if len(chars) > 8 and rng.random() < 0.6:
            shift = rng.randint(-3, 3)
            if shift > 0:
                chars = ([" "] * shift) + chars[:-shift]
            elif shift < 0:
                shift = abs(shift)
                chars = chars[shift:] + ([" "] * shift)
        glitched[y] = "".join(chars)
    return glitched


def _server_room_render_frame(frame_index: int, channel_id: int, env: Optional[Dict[str, Any]] = None) -> str:
    verbose_enabled = bool(server_room_cli_control.get("verbose", False))
    clear_mode = bool(server_room_cli_control.get("clear_mode", False))
    refresh_blank_active = _server_room_cli_is_refresh_blank_active()
    reboot_countdown_active = _server_room_cli_is_reboot_countdown_active()
    reboot_active = _server_room_cli_is_reboot_active()
    burst_active = _server_room_cli_is_burst_active()
    spinner = ("|", "/", "-", "\\")
    spin = spinner[(frame_index + (channel_id % len(spinner))) % len(spinner)]
    depth = 3 + (frame_index % 8)
    recursion_depth = depth * 256
    bars = "".join("#" if j <= (frame_index % 10) else "." for j in range(10))
    phase = (frame_index + channel_id) % 8
    if channel_id == MASTERBOT_CLI_CHANNEL_ID:
        mode = str(server_room_cli_control.get("mode", MASTERBOT_CLI_MODES[0]))
        if mode == "stress":
            depth = min(12, depth + 2)
            recursion_depth = depth * 384
            bars = "".join("#" if j <= ((frame_index + 2) % 10) else "." for j in range(10))
        elif mode == "entropy":
            jitter = (frame_index * 7 + int(server_room_cli_control.get("tick", 0) or 0)) % 9
            depth = 2 + jitter
            recursion_depth = depth * 512
            phase = (phase + jitter) % 8
            spin = random.choice(spinner)
        elif mode == "diagnostic":
            depth = 2
            recursion_depth = depth * 192

    width = 56
    height = 17
    canvas: List[List[str]] = [[" " for _ in range(width)] for _ in range(height)]

    def put(x: int, y: int, ch: str) -> None:
        if 0 <= x < width and 0 <= y < height:
            canvas[y][x] = ch

    def put_text(x: int, y: int, text: str) -> None:
        for i, ch in enumerate(text):
            put(x + i, y, ch)

    def line(x0: int, y0: int, x1: int, y1: int, ch: str) -> None:
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            put(x0, y0, ch)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def draw_box(x: int, y: int, w: int, h: int) -> None:
        line(x, y, x + w - 1, y, "-")
        line(x, y + h - 1, x + w - 1, y + h - 1, "-")
        line(x, y, x, y + h - 1, "|")
        line(x + w - 1, y, x + w - 1, y + h - 1, "|")
        put(x, y, "+")
        put(x + w - 1, y, "+")
        put(x, y + h - 1, "+")
        put(x + w - 1, y + h - 1, "+")

    front_x = 3
    front_y = 7
    front_w = 20
    front_h = 8

    offsets = [(7, -3), (8, -2), (8, -1), (7, 0), (6, 1), (5, 2), (6, 1), (7, 0)]
    off_x, off_y = offsets[phase]
    back_x = front_x + off_x
    back_y = front_y + off_y
    back_w = 17
    back_h = 7

    draw_box(front_x, front_y, front_w, front_h)
    draw_box(back_x, back_y, back_w, back_h)

    corners_front = [
        (front_x, front_y),
        (front_x + front_w - 1, front_y),
        (front_x, front_y + front_h - 1),
        (front_x + front_w - 1, front_y + front_h - 1),
    ]
    corners_back = [
        (back_x, back_y),
        (back_x + back_w - 1, back_y),
        (back_x, back_y + back_h - 1),
        (back_x + back_w - 1, back_y + back_h - 1),
    ]
    for (fx, fy), (bx, by) in zip(corners_front, corners_back):
        slope = "/" if (bx - fx) * (by - fy) < 0 else "\\"
        line(fx, fy, bx, by, slope)

    line(front_x + 2, front_y + 1 + (phase % (front_h - 2)), front_x + front_w - 3, front_y + 1, ".")
    line(back_x + 2, back_y + back_h - 2, back_x + back_w - 3, back_y + 1 + (phase % (back_h - 2)), ".")

    put_text(front_x + 5, front_y + 3, f"{SERVER_ROOM_BACKUP_WORD}()")
    put_text(back_x + 3, back_y + 2, f"{SERVER_ROOM_BACKUP_WORD}()")

    stack_x = 36
    stack_y = 3
    visible_depth = min(6, depth)
    for d in range(visible_depth):
        y = stack_y + d * 2
        put_text(stack_x + (d % 2), y, "+------+")
        put_text(stack_x + (d % 2), y + 1, "|call()|")
    if stack_y + visible_depth * 2 < height:
        put_text(stack_x, min(height - 1, stack_y + visible_depth * 2), f"{spin} x{depth}")

    cube_lines = ["".join(row).rstrip() for row in canvas]
    while cube_lines and not cube_lines[-1]:
        cube_lines.pop()
    if burst_active:
        cube_lines = _server_room_apply_glitch(cube_lines, frame_index, channel_id)

    if clear_mode:
        base_lines = []
    elif refresh_blank_active:
        base_lines = []
    elif reboot_countdown_active:
        now = time.monotonic()
        total = max(1.0, SERVER_ROOM_REBOOT_COUNTDOWN_S)
        remaining = _server_room_cli_reboot_countdown_remaining(now)
        elapsed = max(0.0, total - remaining)
        ratio = max(0.0, min(1.0, elapsed / total))
        width = 28
        done = int(ratio * width)
        bar = ("#" * done) + ("." * (width - done))
        pulse = "◢◣◤◥"[(int(now * 6) + channel_id) % 4]
        base_lines = [
            f"{pulse} soft reboot queued {pulse}",
            f"T-{math.ceil(remaining)}s",
            f"[{bar}]",
            "draining transient jobs and snapshotting runtime state",
            "press 🟢 to cancel reboot",
        ]
    elif reboot_active:
        phase = _server_room_cli_reboot_phase()
        if phase == "drain":
            base_lines = [
                "graceful reboot: draining active streams...",
                "checkpointing frame buffers and telemetry cursors",
            ]
        elif phase == "shutdown":
            base_lines = [
                "graceful reboot: shutting down render workers...",
                "audio bridges and stream taps winding down",
            ]
        elif phase == "boot":
            base_lines = [
                "graceful reboot: boot sequence started",
                "rebuilding runtime state + cursor map",
            ]
        else:
            base_lines = [
                "graceful reboot: warming services",
                "animation will resume shortly",
            ]
    elif channel_id == MASTERBOT_CLI_CHANNEL_ID and bool(server_room_cli_control.get("abort", False)):
        base_lines = [
            ">>> ABORTED <<<",
            "simulation halted by reaction command",
            "issue 🟢 to resume",
        ]
    elif channel_id == MASTERBOT_CLI_CHANNEL_ID and bool(server_room_cli_control.get("paused", False)):
        base_lines = [
            "... paused ...",
            f"idle cursor {MASTERBOT_CLI_CURSOR_STATES[int(server_room_cli_control.get('tick', 0)) % len(MASTERBOT_CLI_CURSOR_STATES)]}",
            "issue 🟢 to resume or ⚡ for burst",
        ]
    elif not verbose_enabled:
        # Minimal display mode: animation only.
        base_lines = [*cube_lines]
    else:
        base_lines = [
            "Server notice: routine backup initiated; recursion detected.",
            f"backupd {spin} depth~{recursion_depth} unwind=[{bars}] phase={phase}",
            *cube_lines,
            "Observed: backup queue is backing up backup queue.",
            "Containment: retries throttled; awaiting patch.",
        ]
    bleed_lines: List[str] = []
    if verbose_enabled and not clear_mode and not refresh_blank_active:
        env_lines = _server_room_env_lines(channel_id, frame_index, env or {})
        if _server_room_controls_enabled(channel_id):
            env_lines = [*_server_room_cli_context_lines(), *env_lines]
            analysis_line = str(server_room_cli_control.get("llm_analysis_line", "") or "").strip()
            analysis_until = float(server_room_cli_control.get("llm_analysis_until", 0.0) or 0.0)
            if analysis_line and analysis_until > time.monotonic():
                env_lines.append(analysis_line)
        entropy_rng = random.Random((frame_index * 313) + channel_id + int(server_room_cli_control.get("tick", 0) or 0))
        if entropy_rng.random() < 0.09:
            drift_ms = entropy_rng.randint(7, 42)
            checksum = f"{entropy_rng.getrandbits(16):04x}"
            env_lines.append(f"[entropy] checksum_mismatch={checksum} timing_drift=+{drift_ms}ms cache=warm")
        voice_lines = _server_room_voice_bleed_lines()
        bleed_lines = [
            *env_lines,
            "------------------------------------------------",
            *voice_lines,
            "------------------------------------------------",
        ]
        if _server_room_controls_enabled(channel_id):
            analyst_text = str(server_room_cli_control.get("llm_analyst_text", "") or "").strip()
            analyst_until = float(server_room_cli_control.get("llm_analyst_until", 0.0) or 0.0)
            if analyst_text and analyst_until > time.monotonic():
                analyst_lines = [ln for ln in analyst_text.splitlines() if ln.strip()]
                bleed_lines.extend(["=== ANALYST ===", *analyst_lines[:4], "------------------------------------------------"])
    elif _server_room_controls_enabled(channel_id) and not clear_mode and not refresh_blank_active:
        analyst_text = str(server_room_cli_control.get("llm_analyst_text", "") or "").strip()
        analyst_until = float(server_room_cli_control.get("llm_analyst_until", 0.0) or 0.0)
        if analyst_text and analyst_until > time.monotonic():
            analyst_lines = [ln for ln in analyst_text.splitlines() if ln.strip()]
            base_lines.extend(["", "=== ANALYST ===", *analyst_lines[:4]])
    control_footer_lines: List[str] = []
    if _server_room_controls_enabled(channel_id):
        control_footer_lines = [
            "------------------------------------------------",
            "[🟢 run ⏸ pause ⚡ burst 🧪 verbose 🔁 refresh 🧹 clear ♻ mode ❌ reboot 🗂 mem 🧾 report 🧠 hint]",
            "report=incident summary | hint=next action",
        ]
    bleed_signature = hashlib.sha1("\n".join(bleed_lines).encode("utf-8")).hexdigest()[:12]
    prev_sig = server_room_bleed_last_signature.get(channel_id)
    if prev_sig != bleed_signature:
        server_room_bleed_last_signature[channel_id] = bleed_signature
        logger.debug(
            "SERVER_ROOM bleed update channel=%s entries=%s signature=%s payload=%s",
            channel_id,
            len(bleed_lines),
            bleed_signature,
            " | ".join(bleed_lines[:5]),
        )

    def render_frame_content(bleed: List[str]) -> str:
        prefix_lines = [*bleed, *base_lines]
        prefix = "\n".join(prefix_lines)
        footer = "\n".join(control_footer_lines)
        if footer:
            body = (prefix + "\n" + footer) if prefix else footer
        else:
            body = prefix
        content = "```text\n" + body + "\n```"
        target_chars = (
            SERVER_ROOM_MESSAGE_TARGET_VERBOSE_CHARS
            if verbose_enabled
            else SERVER_ROOM_MESSAGE_TARGET_CHARS
        )
        if len(content) >= target_chars:
            return content

        # Keep a stable message size so the text box does not jitter frame-to-frame.
        pad_needed = target_chars - len(content)
        pad_line = " " * max(0, pad_needed - 1)
        if footer:
            if prefix:
                body = prefix + "\n" + pad_line + "\n" + footer
            else:
                body = pad_line + "\n" + footer
        else:
            body = body + "\n" + pad_line
        return "```text\n" + body + "\n```"

    def _analyst_protected_indices(lines: List[str]) -> set:
        try:
            idx = lines.index("=== ANALYST ===")
        except ValueError:
            return set()
        # Keep analyst header + up to 4 summary lines + trailing separator.
        return set(range(idx, min(len(lines), idx + 6)))

    content = render_frame_content(bleed_lines)
    while len(content) > SERVER_ROOM_MESSAGE_MAX_CHARS and bleed_lines:
        protected = _analyst_protected_indices(bleed_lines)
        remove_idx: Optional[int] = None
        for i, ln in enumerate(bleed_lines):
            if i in protected:
                continue
            if ln.startswith("[telemetry-bus]") or ln.startswith("- "):
                remove_idx = i
                break
        if remove_idx is None:
            for i in range(len(bleed_lines)):
                if i not in protected:
                    remove_idx = i
                    break
        if remove_idx is None:
            break
        bleed_lines.pop(remove_idx)
        content = render_frame_content(bleed_lines)

    while len(content) > SERVER_ROOM_MESSAGE_MAX_CHARS and len(base_lines) > len(cube_lines):
        # Keep telemetry and stream rows intact; trim prose first.
        logger.debug(
            "SERVER_ROOM frame too long channel=%s chars=%s base_lines=%s; trimming prose section",
            channel_id,
            len(content),
            len(base_lines),
        )
        base_lines.pop()
        content = render_frame_content(bleed_lines)

    if len(content) > SERVER_ROOM_MESSAGE_MAX_CHARS:
        lines = [*bleed_lines, *base_lines]
        analyst_idx = -1
        try:
            analyst_idx = lines.index("=== ANALYST ===")
        except ValueError:
            analyst_idx = -1

        if analyst_idx >= 0:
            head = lines[:12]
            analyst_block = lines[analyst_idx : min(len(lines), analyst_idx + 6)]
            tail = lines[-3:] if len(lines) >= 3 else lines
            trimmed_lines = [*head, "...", *analyst_block, "...", *tail]
        else:
            trimmed_lines = lines[:20] + (["..."] if len(lines) > 22 else []) + lines[-2:]
        trimmed = "\n".join(trimmed_lines)
        if control_footer_lines:
            trimmed = trimmed + "\n" + "\n".join(control_footer_lines)
        content = "```text\n" + trimmed + "\n```"
        logger.warning(
            "SERVER_ROOM frame exceeded Discord limit after bleed trim channel=%s final_chars=%s",
            channel_id,
            len(content),
        )
    return content


async def _server_room_ensure_control_reactions(msg: discord.Message, channel_id: int) -> None:
    if not _server_room_controls_enabled(channel_id):
        return
    try:
        existing: Dict[str, discord.Reaction] = {}
        for reaction in (msg.reactions or []):
            key = _normalize_reaction_emoji(_reaction_obj_name(getattr(reaction, "emoji", None)))
            if key:
                existing[key] = reaction
        missing: List[str] = []
        for emoji in MASTERBOT_CLI_REACTION_EMOJIS:
            key = _normalize_reaction_emoji(emoji)
            current = existing.get(key)
            if current is not None and bool(getattr(current, "me", False)):
                continue
            missing.append(emoji)
            await msg.add_reaction(emoji)
        if missing:
            logger.warning(
                "CLI control reactions restored channel=%s message=%s missing=%s before=%s",
                channel_id,
                msg.id,
                ",".join(missing),
                _reaction_debug_snapshot(msg),
            )
            channel = msg.channel
            if isinstance(channel, discord.TextChannel):
                refreshed = await channel.fetch_message(msg.id)
                logger.info(
                    "CLI control reactions after restore channel=%s message=%s snapshot=%s",
                    channel_id,
                    msg.id,
                    _reaction_debug_snapshot(refreshed),
                )
    except Exception:
        logger.exception("SERVER_ROOM failed adding control reactions channel=%s message=%s", channel_id, msg.id)


async def _server_room_normalize_control_reaction_counts(msg: discord.Message, channel_id: int) -> None:
    if not _server_room_controls_enabled(channel_id):
        return
    if bot.user is None:
        return
    try:
        allowed = {_normalize_reaction_emoji(e) for e in MASTERBOT_CLI_REACTION_EMOJIS}
        removed = 0
        for reaction in (msg.reactions or []):
            key = _normalize_reaction_emoji(_reaction_obj_name(getattr(reaction, "emoji", None)))
            if key not in allowed:
                continue
            if int(getattr(reaction, "count", 0) or 0) <= 1:
                continue
            async for user in reaction.users(limit=100):
                if user.id == bot.user.id:
                    continue
                try:
                    await msg.remove_reaction(reaction.emoji, user)
                    removed += 1
                except Exception:
                    logger.exception(
                        "CLI reaction normalization failed emoji=%s channel=%s message=%s user=%s",
                        key,
                        channel_id,
                        msg.id,
                        user.id,
                    )
    except Exception:
        logger.exception("CLI reaction normalization pass failed channel=%s message=%s", channel_id, msg.id)
        return

    if removed:
        logger.info(
            "CLI reaction normalization removed=%s channel=%s message=%s snapshot=%s",
            removed,
            channel_id,
            msg.id,
            _reaction_debug_snapshot(msg),
        )


async def _server_room_try_edit_message(channel_id: int, msg: discord.Message, frame: str, force: bool = False) -> bool:
    now = time.monotonic()
    state = dict(server_room_render_state.get(channel_id, {}))
    last_ts = float(state.get("last_edit_ts", 0.0) or 0.0)
    last_frame = str(state.get("last_frame", ""))
    similarity = 0.0
    if last_frame:
        similarity = difflib.SequenceMatcher(None, last_frame, frame).ratio()
    rate_hz = max(0.1, _server_room_edit_rate_hz(channel_id))
    min_interval = 1.0 / rate_hz
    backoff_until = float(server_room_cli_control.get("rate_limit_backoff_until", 0.0) or 0.0)
    if not force and now < backoff_until:
        return False
    if not force and (now - last_ts) < min_interval:
        return False
    if not force and frame == last_frame:
        return False
    if not force and similarity >= SERVER_ROOM_DIFF_SIMILARITY_SKIP:
        return False
    try:
        await msg.edit(content=frame)
    except discord.HTTPException as e:
        if int(getattr(e, "status", 0) or 0) == 429:
            hits = int(server_room_cli_control.get("rate_limit_hits", 0) or 0) + 1
            delay = min(SERVER_ROOM_RATE_LIMIT_BACKOFF_MAX_S, SERVER_ROOM_RATE_LIMIT_BACKOFF_BASE_S * (2 ** (hits - 1)))
            server_room_cli_control["rate_limit_hits"] = hits
            server_room_cli_control["rate_limit_backoff_until"] = now + delay
            server_room_cli_control["network_status"] = f"adaptive throttle active ({delay:.1f}s)"
            _server_room_cli_emit_banner("[network] rate-limit adaptive throttle engaged", ttl_s=6.0)
            logger.warning("SERVER_ROOM edit rate-limited channel=%s delay_s=%.1f hits=%s", channel_id, delay, hits)
            return False
        raise

    server_room_cli_control["network_status"] = "nominal"
    server_room_cli_control["rate_limit_hits"] = 0
    server_room_cli_control["rate_limit_backoff_until"] = 0.0
    server_room_render_state[channel_id] = {"last_edit_ts": now, "last_frame": frame}
    return True


async def _server_room_get_status_message(
    channel: discord.TextChannel,
    channel_id: int,
    cache: Dict[int, discord.Message],
) -> Optional[discord.Message]:
    cached = cache.get(channel_id)
    if cached is not None:
        if _server_room_controls_enabled(channel_id):
            try:
                cached = await channel.fetch_message(cached.id)
                cache[channel_id] = cached
            except Exception:
                logger.exception(
                    "SERVER_ROOM failed refreshing cached status message id=%s channel=%s",
                    cached.id,
                    channel_id,
                )
        server_room_active_message_ids[channel_id] = cached.id
        return cached

    pinned_id = SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS.get(channel_id)
    if not pinned_id:
        try:
            pins = await channel.pins()
            pinned_bot = next(
                (m for m in pins if bot.user and m.author.id == bot.user.id and _is_server_room_console_message(m)),
                None,
            )
            if pinned_bot is not None:
                pinned_id = pinned_bot.id
                SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS[channel_id] = pinned_id
                _runtime_config_save()
                logger.info(
                    "SERVER_ROOM discovered pinned status message id=%s channel=%s",
                    pinned_id,
                    channel_id,
                )
        except Exception:
            logger.exception("SERVER_ROOM failed listing pins in channel %s", channel_id)
    if pinned_id:
        try:
            msg = await channel.fetch_message(pinned_id)
            cache[channel_id] = msg
            server_room_active_message_ids[channel_id] = msg.id
            await _server_room_ensure_control_reactions(msg, channel_id)
            logger.info("SERVER_ROOM using pinned message id=%s channel=%s", pinned_id, channel_id)
            return msg
        except Exception:
            logger.exception("SERVER_ROOM failed fetching pinned message %s in channel %s", pinned_id, channel_id)

    try:
        async for msg in channel.history(limit=50):
            if msg.author.id == bot.user.id:
                cache[channel_id] = msg
                server_room_active_message_ids[channel_id] = msg.id
                await _server_room_ensure_control_reactions(msg, channel_id)
                logger.info("SERVER_ROOM reusing prior bot message id=%s channel=%s", msg.id, channel_id)
                return msg
    except Exception:
        logger.exception("SERVER_ROOM failed searching history for reusable status message channel=%s", channel_id)

    try:
        msg = await channel.send(_server_room_render_frame(0, channel_id))
        cache[channel_id] = msg
        server_room_active_message_ids[channel_id] = msg.id
        await _server_room_ensure_control_reactions(msg, channel_id)
        logger.info("SERVER_ROOM created status message id=%s channel=%s", msg.id, channel_id)
        return msg
    except Exception:
        logger.exception("SERVER_ROOM failed creating status message channel=%s", channel_id)
        return None


async def _server_room_ensure_debug_message_for_channel(
    channel: discord.TextChannel,
) -> Tuple[Optional[discord.Message], bool]:
    channel_id = int(channel.id)
    existing: Optional[discord.Message] = None
    replaced = False
    pinned_id = int(SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS.get(channel_id, 0) or 0)
    if pinned_id:
        try:
            existing = await channel.fetch_message(pinned_id)
        except Exception:
            logger.exception(
                "SERVER_ROOM debug ensure failed fetching tracked message id=%s channel=%s",
                pinned_id,
                channel_id,
            )

    if existing is None:
        try:
            pins = await channel.pins()
            pinned_bot = next(
                (m for m in pins if bot.user and m.author.id == bot.user.id and _is_server_room_console_message(m)),
                None,
            )
            if pinned_bot is not None:
                existing = pinned_bot
        except Exception:
            logger.exception("SERVER_ROOM debug ensure failed listing pins channel=%s", channel_id)

    if existing is not None:
        try:
            await existing.delete()
            replaced = True
            logger.info("SERVER_ROOM debug replaced prior message=%s channel=%s", existing.id, channel_id)
        except discord.NotFound:
            pass
        except Exception:
            logger.exception("SERVER_ROOM debug failed deleting prior message=%s channel=%s", existing.id, channel_id)

    msg = await channel.send(_server_room_render_frame(0, channel_id))
    try:
        await msg.pin(reason="Masterbot debug console")
    except Exception:
        logger.exception("SERVER_ROOM debug ensure failed pinning message=%s channel=%s", msg.id, channel_id)
    SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS[channel_id] = int(msg.id)
    if channel_id not in SERVER_ROOM_STATUS_CHANNEL_IDS:
        SERVER_ROOM_STATUS_CHANNEL_IDS.append(channel_id)
    server_room_active_message_ids[channel_id] = msg.id
    server_room_render_state.pop(channel_id, None)
    await _server_room_ensure_control_reactions(msg, channel_id)
    _runtime_config_save()
    _server_room_cli_memory_save()
    _server_room_cli_emit_banner(f"debug console added for channel {channel_id}", ttl_s=10.0)
    logger.info("SERVER_ROOM debug console created message=%s channel=%s", msg.id, channel_id)
    return msg, replaced


async def _server_room_status_loop() -> None:
    global masterbot_soft_reboot_task
    logger.info("SERVER_ROOM loop starting channels=%s", SERVER_ROOM_STATUS_CHANNEL_IDS)
    messages: Dict[int, discord.Message] = {}
    frame_index_by_channel: Dict[int, int] = {}
    try:
        while True:
            _server_room_cli_process_interrupts(max_events=8)
            env_snapshot = await _server_room_refresh_env_snapshot_if_stale()
            _server_room_schedule_llm_cycle(env_snapshot)
            server_room_cli_control["tick"] = int(server_room_cli_control.get("tick", 0) or 0) + 1
            now = time.monotonic()
            refresh_blank_active = _server_room_cli_is_refresh_blank_active()
            reboot_countdown_active = _server_room_cli_is_reboot_countdown_active(now)
            if (
                bool(server_room_cli_control.get("abort", False))
                and not reboot_countdown_active
                and not bool(server_room_cli_control.get("soft_reboot_in_progress", False))
            ):
                if masterbot_soft_reboot_task is None or masterbot_soft_reboot_task.done():
                    masterbot_soft_reboot_task = asyncio.create_task(
                        _masterbot_soft_reboot(reason="reaction:countdown"),
                        name="masterbot-soft-reboot",
                    )
            cli_refresh = bool(server_room_cli_control.get("refresh_requested", False) and not refresh_blank_active)
            if cli_refresh:
                for ch_id in SERVER_ROOM_STATUS_CHANNEL_IDS:
                    frame_index_by_channel[ch_id] = 0
                    server_room_render_state.pop(ch_id, None)
                server_room_cli_control["refresh_requested"] = False

            for channel_id in SERVER_ROOM_STATUS_CHANNEL_IDS:
                channel = await _server_room_resolve_text_channel(channel_id)
                if channel is None:
                    continue
                msg = await _server_room_get_status_message(channel, channel_id, messages)
                if msg is None:
                    continue
                try:
                    if _server_room_controls_enabled(channel_id):
                        now = time.monotonic()
                        maintenance = dict(server_room_reaction_maintenance.get(channel_id, {}))
                        last_reaction_refresh_ts = float(maintenance.get("last_reaction_refresh_ts", 0.0) or 0.0)
                        if (now - last_reaction_refresh_ts) >= SERVER_ROOM_REACTION_REFRESH_S:
                            await _server_room_ensure_control_reactions(msg, channel_id)
                            maintenance["last_reaction_refresh_ts"] = now
                        last_reaction_normalize_ts = float(maintenance.get("last_reaction_normalize_ts", 0.0) or 0.0)
                        if (now - last_reaction_normalize_ts) >= SERVER_ROOM_REACTION_NORMALIZE_S:
                            await _server_room_normalize_control_reaction_counts(msg, channel_id)
                            maintenance["last_reaction_normalize_ts"] = now
                        server_room_reaction_maintenance[channel_id] = maintenance

                    frame_index = int(frame_index_by_channel.get(channel_id, 0) or 0)
                    frame = _server_room_render_frame(frame_index, channel_id, env_snapshot)
                    force = bool(cli_refresh)
                    edited = await _server_room_try_edit_message(channel_id, msg, frame, force=force)
                    if edited:
                        hold_frames = (
                            bool(server_room_cli_control.get("paused", False))
                            or bool(server_room_cli_control.get("abort", False))
                            or bool(server_room_cli_control.get("clear_mode", False))
                            or _server_room_cli_is_refresh_blank_active()
                            or _server_room_cli_is_reboot_countdown_active()
                        ) and not _server_room_cli_is_burst_active()
                        if not hold_frames:
                            frame_index_by_channel[channel_id] = frame_index + 1
                except discord.NotFound:
                    messages.pop(channel_id, None)
                    server_room_active_message_ids.pop(channel_id, None)
                    logger.warning("SERVER_ROOM tracked message missing; will recreate channel=%s", channel_id)
                except discord.Forbidden:
                    logger.exception("SERVER_ROOM missing permissions to edit message channel=%s", channel_id)
                except Exception:
                    logger.exception("SERVER_ROOM failed editing message channel=%s", channel_id)
                if bool(server_room_cli_control.get("paused", False)) and not _server_room_cli_is_burst_active():
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                else:
                    await asyncio.sleep(0.2)

            mode = str(server_room_cli_control.get("mode", MASTERBOT_CLI_MODES[0]))
            if _server_room_cli_is_burst_active():
                loop_sleep = 0.9
            elif mode == "stress":
                loop_sleep = 2.5
            elif mode == "entropy":
                loop_sleep = float(random.choice([1.8, 2.4, 3.0]))
            elif mode == "diagnostic":
                loop_sleep = 7.0
            else:
                loop_sleep = 4.0
            await asyncio.sleep(loop_sleep)
    except asyncio.CancelledError:
        logger.info("SERVER_ROOM loop cancelled")
        raise
    except Exception:
        logger.exception("SERVER_ROOM unexpected error in status loop")


def _voice_preset_targets() -> Tuple[List[discord.abc.Connectable], List[str]]:
    targets: List[discord.abc.Connectable] = []
    warnings: List[str] = []
    for channel_id in VOICE_LOOP_PRESET_CHANNEL_IDS:
        ch = bot.get_channel(channel_id)
        if ch is None:
            warnings.append(f"Preset channel not found: id={channel_id}")
            continue
        if not isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            warnings.append(f"Preset channel is not voice/stage: {getattr(ch, 'name', '<unknown>')} ({channel_id})")
            continue
        targets.append(ch)
    return targets, warnings


async def _voice_disconnect_guild(guild: discord.Guild) -> None:
    vc = discord.utils.get(bot.voice_clients, guild=guild)
    if not vc:
        return
    try:
        if vc.is_playing() or vc.is_paused():
            vc.stop()
    except Exception:
        pass
    with contextlib.suppress(Exception):
        await vc.disconnect(force=True)


async def _voice_play_one(vc: discord.VoiceClient, media_path: Path) -> None:
    if not media_path.exists():
        raise FileNotFoundError(str(media_path))
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed or not in PATH")

    loop = asyncio.get_running_loop()
    done: asyncio.Future = loop.create_future()

    # Let ffmpeg decode any container/codec and emit PCM for Discord.
    source = discord.FFmpegPCMAudio(str(media_path), before_options="-nostdin")

    def _after_play(err: Optional[Exception]) -> None:
        if done.done():
            return
        loop.call_soon_threadsafe(done.set_result, err)

    vc.play(source, after=_after_play)
    err = await done
    if err:
        raise RuntimeError(f"Playback failed for {media_path.name}: {err}")


def _voice_is_not_connected_error(exc: Exception) -> bool:
    return isinstance(exc, discord.ClientException) and "Not connected to voice" in str(exc)


def _voice_event_append(event_type: str, **fields: Any) -> None:
    now = time.time()
    event = {
        "ts_epoch": now,
        "ts_iso": datetime.datetime.utcfromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event_type": event_type,
        **fields,
    }
    with voice_event_lock:
        payload = {"updated_at": event["ts_iso"], "events": []}
        if VOICE_EVENTS_RECENT_PATH.exists():
            try:
                obj = json.loads(VOICE_EVENTS_RECENT_PATH.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    payload = obj
            except Exception:
                logger.exception("VOICE event log load failed path=%s", VOICE_EVENTS_RECENT_PATH)
        events = payload.get("events")
        if not isinstance(events, list):
            events = []
        events.append(event)
        if len(events) > VOICE_EVENTS_RECENT_MAX:
            events = events[-VOICE_EVENTS_RECENT_MAX:]
        payload["events"] = events
        payload["updated_at"] = event["ts_iso"]
        try:
            tmp = VOICE_EVENTS_RECENT_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            tmp.replace(VOICE_EVENTS_RECENT_PATH)
        except Exception:
            logger.exception("VOICE event log save failed path=%s", VOICE_EVENTS_RECENT_PATH)


def _voice_failure_record(reason: str, **fields: Any) -> None:
    now = time.time()
    cutoff = now - (VOICE_FAILURE_WINDOW_DAYS * 86400)
    entry = {
        "ts_epoch": now,
        "ts_iso": datetime.datetime.utcfromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": reason,
        **fields,
    }
    with voice_event_lock:
        payload = {"window_days": VOICE_FAILURE_WINDOW_DAYS, "updated_at": entry["ts_iso"], "failures": []}
        if VOICE_FAILURES_PATH.exists():
            try:
                obj = json.loads(VOICE_FAILURES_PATH.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    payload = obj
            except Exception:
                logger.exception("VOICE failure log load failed path=%s", VOICE_FAILURES_PATH)
        failures = payload.get("failures")
        if not isinstance(failures, list):
            failures = []
        failures = [f for f in failures if float(f.get("ts_epoch", 0.0) or 0.0) >= cutoff]
        failures.append(entry)
        payload["failures"] = failures
        payload["updated_at"] = entry["ts_iso"]
        payload["window_days"] = VOICE_FAILURE_WINDOW_DAYS
        payload["total_failures_7d"] = len(failures)
        payload["instability_frequency_per_day"] = round(len(failures) / float(VOICE_FAILURE_WINDOW_DAYS), 3)
        try:
            tmp = VOICE_FAILURES_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            tmp.replace(VOICE_FAILURES_PATH)
        except Exception:
            logger.exception("VOICE failure log save failed path=%s", VOICE_FAILURES_PATH)
    _voice_event_append("failure", reason=reason, **fields)


async def _voice_generate_incident_report(since_minutes: int = 30) -> Tuple[str, Optional[Path]]:
    script_path = Path(__file__).with_name("voice_incident_report.py")
    if not script_path.exists():
        return (f"voice report script missing: {script_path}", None)
    since = max(1, int(since_minutes))
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script_path),
        "--since",
        str(since),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    out = out_b.decode("utf-8", errors="replace").strip()
    err = err_b.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        body = (err or out or "unknown error").strip()
        return (f"voice report failed (exit={proc.returncode}): {body[:1200]}", None)

    report_path: Optional[Path] = None
    summary_lines: List[str] = []
    for line in out.splitlines():
        if line.startswith("JSON_PATH:"):
            candidate = line.split(":", 1)[1].strip()
            if candidate:
                report_path = Path(candidate)
            continue
        summary_lines.append(line)
    if err:
        summary_lines.append(f"[stderr] {err}")
    summary = "\n".join(summary_lines).strip()
    return (summary or "voice report completed.", report_path)


async def _voice_ensure_connected(target: discord.abc.Connectable) -> discord.VoiceClient:
    guild = target.guild
    last_exc: Optional[Exception] = None
    retry_delays = [1.0, 3.0, 5.0, 8.0]
    lock = voice_connect_locks.get(guild.id)
    if lock is None:
        lock = asyncio.Lock()
        voice_connect_locks[guild.id] = lock

    for attempt in range(1, VOICE_CONNECT_RETRIES + 1):
        try:
            async with lock:
                existing = discord.utils.get(bot.voice_clients, guild=guild)
                if existing and existing.is_connected():
                    if existing.channel and existing.channel.id != target.id:
                        await existing.move_to(target)
                    return existing

                # Clean up stale/in-progress clients before a fresh connect attempt.
                if existing is not None:
                    with contextlib.suppress(Exception):
                        if existing.is_playing() or existing.is_paused():
                            existing.stop()
                    with contextlib.suppress(Exception):
                        await existing.disconnect(force=True)
                    await asyncio.sleep(0.8)

                vc = await target.connect()
                await asyncio.sleep(0.4)
                _voice_event_append(
                    "connect_success",
                    guild_id=int(guild.id),
                    channel_id=int(target.id),
                    active_voice_clients=len(bot.voice_clients),
                )
                return vc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc
            delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
            logger.warning(
                "VOICE connect retry guild=%s channel=%s attempt=%s/%s delay_s=%.1f err=%r",
                guild.id,
                target.id,
                attempt,
                VOICE_CONNECT_RETRIES,
                delay,
                exc,
            )
            _voice_failure_record(
                "connect_retry",
                guild_id=int(guild.id),
                channel_id=int(target.id),
                attempt=int(attempt),
                delay_s=float(delay),
                error=repr(exc),
                active_voice_clients=len(bot.voice_clients),
            )
            if attempt < VOICE_CONNECT_RETRIES:
                await asyncio.sleep(delay)
    _voice_failure_record(
        "connect_failed",
        guild_id=int(guild.id),
        channel_id=int(target.id),
        attempts=int(VOICE_CONNECT_RETRIES),
        error=repr(last_exc),
        active_voice_clients=len(bot.voice_clients),
    )
    raise RuntimeError(f"voice connect failed guild={guild.id} channel={target.id} after {VOICE_CONNECT_RETRIES} attempts") from last_exc


def _discover_voice_media_paths(exclude_names: Optional[set[str]] = None) -> List[Path]:
    if not VOICE_LOOP_MEDIA_DIR.exists():
        return []
    exclusions = {str(x).strip().lower() for x in (exclude_names or set()) if str(x).strip()}
    media_paths: List[Path] = []
    for path in VOICE_LOOP_MEDIA_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.name.lower() in exclusions:
            continue
        if path.suffix.lower() not in VOICE_LOOP_PLAYABLE_EXTENSIONS:
            continue
        media_paths.append(path)
    return sorted(media_paths, key=lambda p: str(p).lower())


async def _voice_loop_worker(target: discord.abc.Connectable) -> None:
    guild = target.guild
    logger.info("VOICE loop starting guild=%s channel=%s", guild.id, target.id)
    _voice_event_append("loop_start", guild_id=int(guild.id), channel_id=int(target.id))
    intro_done = False
    intro_names: set[str] = set()
    consecutive_connect_failures = 0
    max_connect_failures = 3
    try:
        while True:
            if not intro_done:
                intro_paths = [p for p in VOICE_LOOP_BOOT_INTRO_FILES if p.exists()]
                intro_names = {p.name.lower() for p in intro_paths}
                for intro_path in intro_paths:
                    try:
                        vc = await _voice_ensure_connected(target)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        consecutive_connect_failures += 1
                        logger.exception(
                            "VOICE intro connect failed guild=%s channel=%s file=%s; recovering",
                            guild.id,
                            target.id,
                            intro_path,
                        )
                        if consecutive_connect_failures >= max_connect_failures:
                            logger.error(
                                "VOICE intro loop disabled guild=%s channel=%s after %s connect failures",
                                guild.id,
                                target.id,
                                consecutive_connect_failures,
                            )
                            consecutive_connect_failures = 0
                            await asyncio.sleep(max(8.0, VOICE_RECOVER_SLEEP_S))
                            intro_done = True
                            break
                        # Best-effort intro: if connect is unstable, move on to normal rotation.
                        intro_done = True
                        _voice_failure_record(
                            "intro_connect_failed",
                            guild_id=int(guild.id),
                            channel_id=int(target.id),
                            consecutive_connect_failures=int(consecutive_connect_failures),
                            active_voice_clients=len(bot.voice_clients),
                        )
                        await asyncio.sleep(VOICE_RECOVER_SLEEP_S)
                        break
                    voice_now_playing[guild.id] = {
                        "channel_id": int(target.id),
                        "channel_name": str(getattr(target, "name", "unknown")),
                        "media_path": str(intro_path),
                        "media_name": intro_path.name,
                        "started_at": time.time(),
                    }
                    logger.info(
                        "VOICE boot intro guild=%s channel=%s file=%s",
                        guild.id,
                        target.id,
                        intro_path,
                    )
                    try:
                        await _voice_play_one(vc, intro_path)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if _voice_is_not_connected_error(exc):
                            logger.warning(
                                "VOICE intro lost connection guild=%s channel=%s file=%s; reconnecting",
                                guild.id,
                                target.id,
                                intro_path,
                            )
                            await _voice_disconnect_guild(guild)
                            await asyncio.sleep(min(1.5, VOICE_RECOVER_SLEEP_S))
                            # Best-effort intro only; do not let intro errors block normal rotation.
                            intro_done = True
                            _voice_failure_record(
                                "intro_lost_connection",
                                guild_id=int(guild.id),
                                channel_id=int(target.id),
                                file=intro_path.name,
                                active_voice_clients=len(bot.voice_clients),
                            )
                            break
                        logger.exception("VOICE boot intro failed guild=%s file=%s", guild.id, intro_path)
                        _voice_failure_record(
                            "intro_playback_failed",
                            guild_id=int(guild.id),
                            channel_id=int(target.id),
                            file=intro_path.name,
                            error=repr(exc),
                            active_voice_clients=len(bot.voice_clients),
                        )
                        await asyncio.sleep(VOICE_RECOVER_SLEEP_S)
                        intro_done = True
                        break
                    await asyncio.sleep(0.2)
                else:
                    intro_done = True
                    logger.info("VOICE boot intro sequence complete guild=%s channel=%s", guild.id, target.id)
                    continue
                continue

            cycle = _discover_voice_media_paths(exclude_names=intro_names)
            if not cycle:
                cycle = _discover_voice_media_paths()
            if not cycle:
                logger.warning("VOICE no playable media files found under %s", VOICE_LOOP_MEDIA_DIR)
                await asyncio.sleep(5.0)
                continue
            random.shuffle(cycle)
            for media_path in cycle:
                try:
                    vc = await _voice_ensure_connected(target)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    consecutive_connect_failures += 1
                    logger.exception(
                        "VOICE connect failed guild=%s channel=%s before file=%s; recovering",
                        guild.id,
                        target.id,
                        media_path,
                    )
                    if consecutive_connect_failures >= max_connect_failures:
                        logger.error(
                            "VOICE loop disabled guild=%s channel=%s after %s connect failures",
                            guild.id,
                            target.id,
                            consecutive_connect_failures,
                        )
                        consecutive_connect_failures = 0
                        await asyncio.sleep(max(8.0, VOICE_RECOVER_SLEEP_S))
                        break
                    _voice_failure_record(
                        "loop_connect_failed",
                        guild_id=int(guild.id),
                        channel_id=int(target.id),
                        file=media_path.name,
                        consecutive_connect_failures=int(consecutive_connect_failures),
                        active_voice_clients=len(bot.voice_clients),
                    )
                    await asyncio.sleep(VOICE_RECOVER_SLEEP_S)
                    break
                voice_now_playing[guild.id] = {
                    "channel_id": int(target.id),
                    "channel_name": str(getattr(target, "name", "unknown")),
                    "media_path": str(media_path),
                    "media_name": media_path.name,
                    "started_at": time.time(),
                }
                logger.debug(
                    "VOICE state update guild=%s channel=%s media=%s active_snapshots=%s",
                    guild.id,
                    target.id,
                    media_path.name,
                    len(voice_now_playing),
                )
                logger.info(
                    "VOICE playing guild=%s channel=%s file=%s",
                    guild.id,
                    target.id,
                    media_path,
                )
                consecutive_connect_failures = 0
                try:
                    await _voice_play_one(vc, media_path)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if _voice_is_not_connected_error(exc):
                        logger.warning(
                            "VOICE lost connection guild=%s channel=%s file=%s; reconnecting",
                            guild.id,
                            target.id,
                            media_path,
                        )
                        await _voice_disconnect_guild(guild)
                        await asyncio.sleep(min(1.5, VOICE_RECOVER_SLEEP_S))
                        _voice_failure_record(
                            "playback_lost_connection",
                            guild_id=int(guild.id),
                            channel_id=int(target.id),
                            file=media_path.name,
                            active_voice_clients=len(bot.voice_clients),
                        )
                        continue
                    logger.exception(
                        "VOICE playback failed guild=%s channel=%s file=%s; recovering",
                        guild.id,
                        target.id,
                        media_path,
                    )
                    _voice_failure_record(
                        "playback_failed",
                        guild_id=int(guild.id),
                        channel_id=int(target.id),
                        file=media_path.name,
                        error=repr(exc),
                        active_voice_clients=len(bot.voice_clients),
                    )
                    await asyncio.sleep(VOICE_RECOVER_SLEEP_S)
                    break
                await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        logger.info("VOICE loop cancelled guild=%s", guild.id)
        raise
    except Exception:
        logger.exception("VOICE loop failed guild=%s", guild.id)
    finally:
        voice_now_playing.pop(guild.id, None)
        logger.debug("VOICE state cleared guild=%s remaining_snapshots=%s", guild.id, len(voice_now_playing))
        await _voice_disconnect_guild(guild)
        voice_loop_tasks.pop(guild.id, None)
        _voice_event_append("loop_stop", guild_id=int(guild.id), channel_id=int(target.id))
        logger.info("VOICE loop stopped guild=%s", guild.id)


async def _voice_start_loop_for_channel(target: discord.abc.Connectable) -> str:
    guild = target.guild
    media_paths = _discover_voice_media_paths()
    if not media_paths:
        ext_csv = ", ".join(sorted(VOICE_LOOP_PLAYABLE_EXTENSIONS))
        raise FileNotFoundError(
            f"No playable media files found under {VOICE_LOOP_MEDIA_DIR} "
            f"(extensions: {ext_csv})"
        )

    old_task = voice_loop_tasks.get(guild.id)
    if old_task and not old_task.done():
        old_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await old_task

    task = asyncio.create_task(_voice_loop_worker(target), name=f"voice-loop-{guild.id}")
    voice_loop_tasks[guild.id] = task
    return (
        f"Started loop in **{guild.name}** -> **{target.name}** (`{target.id}`) "
        f"with {len(media_paths)} media files from `{VOICE_LOOP_MEDIA_DIR}`"
    )


async def _voice_start_preset_loops() -> List[str]:
    targets, warnings = _voice_preset_targets()
    lines: List[str] = []
    for warning in warnings:
        logger.warning("VOICE preset warning: %s", warning)
        lines.append(f"Warning: {warning}")
    for target in targets:
        try:
            lines.append(await _voice_start_loop_for_channel(target))
            await asyncio.sleep(8.0)
        except Exception as e:
            msg = f"Failed for {target.guild.name} / {target.name}: {e}"
            logger.exception("VOICE preset start failed: %s", msg)
            lines.append(f"Warning: {msg}")
    return lines


async def _voice_stop_loop_for_guild(guild: discord.Guild) -> bool:
    task = voice_loop_tasks.get(guild.id)
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return True
    await _voice_disconnect_guild(guild)
    return False


async def _masterbot_soft_reboot(reason: str = "manual") -> None:
    global server_room_status_task, masterbot_soft_reboot_task
    if bool(server_room_cli_control.get("soft_reboot_in_progress", False)):
        return

    server_room_cli_control["soft_reboot_in_progress"] = True
    started_at = time.monotonic()
    server_room_cli_control["reboot_countdown_started_at"] = 0.0
    server_room_cli_control["reboot_countdown_until"] = 0.0
    server_room_cli_control["reboot_started_at"] = started_at
    server_room_cli_control["reboot_until"] = started_at + SERVER_ROOM_REBOOT_DURATION_S
    logger.warning("SOFT_REBOOT start reason=%s", reason)

    try:
        await asyncio.sleep(1.0)

        # Stop the server-room renderer task so state restarts cleanly.
        task = server_room_status_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        server_room_status_task = None

        # Cancel all voice loops and disconnect any leftover voice clients.
        for guild_id, task in list(voice_loop_tasks.items()):
            if task is not None and not task.done():
                logger.info("SOFT_REBOOT cancelling voice loop guild=%s", guild_id)
                task.cancel()
        for guild_id, task in list(voice_loop_tasks.items()):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for vc in list(bot.voice_clients):
            with contextlib.suppress(Exception):
                if vc.is_playing() or vc.is_paused():
                    vc.stop()
            with contextlib.suppress(Exception):
                await vc.disconnect(force=True)
        voice_loop_tasks.clear()
        voice_now_playing.clear()

        # Reset renderer and control caches.
        server_room_active_message_ids.clear()
        server_room_render_state.clear()
        server_room_bleed_last_signature.clear()
        server_room_reaction_maintenance.clear()
        llm_task = server_room_cli_control.get("llm_task")
        if isinstance(llm_task, asyncio.Task) and not llm_task.done():
            llm_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await llm_task
        server_room_cli_control["llm_task"] = None
        server_room_cli_control["llm_manual_request"] = None
        server_room_cli_control["llm_analysis_line"] = ""
        server_room_cli_control["llm_analysis_until"] = 0.0
        server_room_cli_control["llm_analyst_text"] = ""
        server_room_cli_control["llm_analyst_until"] = 0.0
        server_room_cli_control["llm_last_event_hash"] = ""
        server_room_cli_control["llm_last_semantic_state"] = {}
        server_room_cli_control["tick"] = 0
        server_room_cli_control["refresh_blank_until"] = time.monotonic() + 1.5
        server_room_cli_control["interrupt_queue"] = []
        server_room_cli_control["interrupt_last_ts"] = {}
        server_room_cli_control["active_controller_user_id"] = 0
        server_room_cli_control["active_controller_until"] = 0.0
        server_room_cli_control["network_status"] = "nominal"
        server_room_cli_control["rate_limit_backoff_until"] = 0.0
        server_room_cli_control["rate_limit_hits"] = 0

        # Restart server-room renderer.
        server_room_status_task = asyncio.create_task(
            _server_room_status_loop(),
            name="server-room-status-loop",
        )

        # Restart preset voice loops.
        results = await _voice_start_preset_loops()
        for line in results:
            logger.warning("SOFT_REBOOT voice: %s", line)

        server_room_cli_control["abort"] = False
        server_room_cli_control["paused"] = False
        server_room_cli_control["clear_mode"] = False
        server_room_cli_control["refresh_requested"] = True
        server_room_cli_control["reboot_started_at"] = 0.0
        server_room_cli_control["reboot_until"] = 0.0
        _server_room_cli_emit_banner("soft reboot complete")
        _server_room_cli_memory_save()
        logger.warning("SOFT_REBOOT complete reason=%s", reason)
    except Exception:
        logger.exception("SOFT_REBOOT failed reason=%s", reason)
        _server_room_cli_emit_banner("soft reboot failed; runtime preserved")
    finally:
        server_room_cli_control["soft_reboot_in_progress"] = False
        masterbot_soft_reboot_task = None


async def _sol_generate_response(ctx: commands.Context, question: str) -> str:
    mode = _sol_get_mode(int(ctx.author.id))
    scope = "dm" if ctx.guild is None else "guild"
    hist = _sol_get_history(int(ctx.author.id), scope)
    myth = _sol_user_myth_state(ctx)
    telemetry = _sol_telemetry_snapshot()

    matches = await sol_engine.search(question, top_k=4) if sol_engine.index_ready else []
    snippets = []
    for m in matches:
        snippet = m["text"].replace("\n", " ").strip()[:240]
        snippets.append(f"[{m['path']}#{m['chunk_index']}] {snippet}")

    sys_text = (
        "You are SOL, an embedded subsystem inside MasterBot running in Discord. "
        "Never claim real-world abilities you lack or imply hidden access. Never reveal secrets, tokens, environment variables, or private system internals. "
        "You may reference level/xp/achievements/newrules/transmigration and suggest commands, but you must not mutate XP, achievements, or newrules. "
        "Roles: assistant+narrator+memory engine+governance advisor+planner. "
        "Narrative screenplay style is allowed only when asked, or in verbose/oracle mode."
    )
    mode_map = {
        "quiet": "Respond concisely in 2-4 sentences.",
        "normal": "Respond clearly with direct answer, then one suggested next step.",
        "verbose": "Respond with rich context and optional screenplay-flavored section.",
        "oracle": "Respond as sardonic yet mythic systems oracle with structured screenplay-flavored sections and cryptic caveats.",
    }
    input_text = (
        f"Mode: {mode}\n"
        f"Question: {question}\n"
        f"MythState: {myth}\n"
        f"Telemetry: {telemetry}\n"
        f"RecentHistory: {hist[-8:]}\n"
        f"SemanticMatches: {snippets if snippets else ['(none)']}\n"
        "When relevant, quote short snippets from SemanticMatches and mention their source labels."
    )

    def _call_responses() -> str:
        model = "gpt-4.1-mini"
        style_text = mode_map.get(mode, mode_map["oracle"])

        # Prefer Responses API, but fall back to Chat Completions for older SDKs.
        if hasattr(openai_client, "responses"):
            resp = openai_client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": sys_text}]},
                    {"role": "system", "content": [{"type": "input_text", "text": style_text}]},
                    {"role": "user", "content": [{"type": "input_text", "text": input_text}]},
                ],
                temperature=0.6,
            )
            return (resp.output_text or "").strip()

        resp = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_text},
                {"role": "system", "content": style_text},
                {"role": "user", "content": input_text},
            ],
            temperature=0.6,
        )
        content = resp.choices[0].message.content if resp.choices else ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [item.get("text", "") for item in content if isinstance(item, dict)]
            return "\n".join(part for part in parts if part).strip()
        return ""

    answer = await asyncio.to_thread(_call_responses)
    _sol_append_history(int(ctx.author.id), scope, "user", question)
    _sol_append_history(int(ctx.author.id), scope, "assistant", answer)
    return answer or "I have no stable answer yet. Try reframing your question."


# ----------------------------
# RPG math
# ----------------------------
def ability_mod(score: int) -> int:
    # Old code used a big if/elif ladder. Same outcome is floor((score - 10)/2).
    # But we preserve the spirit and clamp plausibly.
    if score <= 1:
        return -5
    return (score - 10) // 2


DICE_TOKEN_RE = re.compile(r"(?P<num>-?\d*)d(?P<sides>-?\d+)", re.IGNORECASE)

ALLOWED_OPS = {"+", "-", "*", "/", "//", "**"}

def _normalize_ops(expr: str) -> str:
    # Old scripts allowed x, ×, ÷, ^. Normalize.
    expr = expr.replace("×", "*").replace("x", "*").replace("X", "*")
    expr = expr.replace("÷", "/")
    expr = expr.replace("^", "**")
    return expr


def parse_roll_expression(expr: str) -> Tuple[List[int], str, int]:
    """
    Supports:
      3d6
      d20
      -2d8
      4d6+2
      2d20+5*2   (math applied to the summed roll)
    Returns: (roll_list, math_string, result_int)
    """
    expr = _normalize_ops(expr.strip())
    m = DICE_TOKEN_RE.search(expr)
    if not m:
        raise ValueError("No dice expression found (expected NdM like 2d20).")

    num_str = m.group("num")
    sides_str = m.group("sides")

    dice = int(num_str) if num_str not in ("", "+", "-") else 1
    sides = int(sides_str)

    # sane-ish limits (from old scripts)
    if abs(dice) > 100000:
        raise ValueError("Too many dice (>100000).")
    if abs(sides) > 10000:
        raise ValueError("Too many sides (>10000).")

    # roll list
    rolls: List[int] = []
    if dice == 0:
        rolls = [0]
    else:
        sign = -1 if dice < 0 else 1
        count = abs(dice)
        if sides == 0:
            rolls = [0] * count
        else:
            ssign = -1 if sides < 0 else 1
            sabs = abs(sides)
            for _ in range(count):
                r = random.randint(1, sabs) * ssign * sign
                rolls.append(r)

    base = sum(rolls)

    # Now apply tail math safely: we only allow chaining with numbers and ops.
    # We DO NOT eval arbitrary Python.
    tail = expr[m.end():].strip()
    result = base
    math_string = str(base)

    if tail:
        # tokenize: operators and integers
        tokens = re.findall(r"(\*\*|//|[+\-*/]|\d+)", tail)
        if not tokens:
            raise ValueError("Invalid math tail after dice.")
        # must be op, num, op, num...
        if tokens[0] not in ALLOWED_OPS:
            raise ValueError("Math tail must start with an operator (e.g. +2, *3).")
        if len(tokens) % 2 != 0:
            raise ValueError("Math tail must be operator/number pairs (e.g. +2*3).")

        i = 0
        while i < len(tokens):
            op = tokens[i]
            n = int(tokens[i + 1])
            math_string += f"{op}{n}"
            if op == "+":
                result = result + n
            elif op == "-":
                result = result - n
            elif op == "*":
                result = result * n
            elif op == "/":
                if n == 0:
                    raise ValueError("Division by zero.")
                result = int(result / n)
            elif op == "//":
                if n == 0:
                    raise ValueError("Division by zero.")
                result = result // n
            elif op == "**":
                # prevent “raise the universe to itself” nonsense
                if abs(n) > 12:
                    raise ValueError("Exponent too large (abs > 12).")
                result = int(result ** n)
            else:
                raise ValueError("Unsupported operator.")
            i += 2

    return rolls, math_string, int(result)


# ----------------------------
# XP / Achievement engine
# ----------------------------
@dataclass
class XPResult:
    leveled_up: bool
    old_level: int
    new_level: int
    newrules_activated: bool
    achievement_msgs: List[str]


async def update_xp_from_message(
    guild: discord.Guild,
    author: discord.Member | discord.User,
    message_content_lower: str,
    ctx_channel: discord.abc.Messageable,
) -> XPResult:
    """
    Mirrors the old logic:
    - wordcount increments, per-word frequency stored
    - xp formula differs based on newrules/transmigration state
    - achievements: Lost The Game, NOT a 0, 42, To the Moon, Ya broke physics..., DELETION -> TRANSMIGRATION
    """
    # word parsing: old used r'\w+'
    words = re.findall(r"\w+", message_content_lower)
    msg_wc = len(words)

    achievement_msgs: List[str] = []
    pending_posts: List[Tuple[discord.abc.Messageable, str]] = []
    leveled_up = False
    newrules_activated = False

    async def post_to(env_prefix: str) -> discord.abc.Messageable:
        return await resolve_post_channel(ctx_channel=ctx_channel, guild=guild, env_prefix=env_prefix)

    leaderboard = await post_to("MASTERBOT_LEADERBOARD")
    welcome = await post_to("MASTERBOT_WELCOME")

    with _open_db() as db:
        server_bucket = _get_server_bucket(db, guild.id)
        user_bucket = _get_user_bucket(server_bucket, int(author.id))

        level = int(user_bucket.get("level", 0))
        xp = int(user_bucket.get("xp", 1))
        inspiration = int(user_bucket.get("inspiration", 0))
        dicerolls = int(user_bucket.get("dicerolls", 1))
        wordcount = int(user_bucket.get("wordcount", 0))
        wordmap: Dict[str, int] = dict(user_bucket.get("words", {}))
        achievements: List[str] = list(user_bucket.get("achievements", []))

        # cleanup legacy typos
        achievements = [a.replace("DELETED", "DELETION").replace("DELETEION", "DELETION") for a in achievements]

        # update word stats
        for w in words:
            wordmap[w] = wordmap.get(w, 0) + 1
        wordcount += msg_wc

        # global-ish counters for newrules trigger
        users = server_bucket.get("users", {})
        all_ach: List[str] = []
        all_dice: List[int] = []
        for ub in users.values():
            all_dice.append(int(ub.get("dicerolls", 1)))
            for a in ub.get("achievements", []):
                all_ach.append(str(a))

        transmigrations = all_ach.count("TRANSMIGRATION")
        user_count = max(len(users), 1)
        newrules = bool(server_bucket.get("newrules", False))

        dice_sorted = sorted(all_dice, reverse=True) if all_dice else [dicerolls]
        try:
            diceroll_position = dice_sorted.index(dicerolls) + 1
        except ValueError:
            diceroll_position = len(dice_sorted)

        # newrules activation
        if transmigrations > int(user_count / 2) and not newrules:
            newrules = True
            newrules_activated = True
            pending_posts.append((leaderboard, "yeeess... new rules are at play, indeed"))

        prev_level = level

        # xp formula
        if newrules or ("TRANSMIGRATION" in achievements):
            advantage = int(20 / max(diceroll_position, 1)) + (inspiration * level)
            xp = int(xp + ((msg_wc + advantage) // max(level + 1, 1)))
            level = int(xp // 444)
        else:
            xp = int(xp + ((msg_wc + random.randint(1, max(dicerolls, 1))) // max(level + 1, 1)) + (inspiration * level))
            level = int(xp // 111)

        if level > prev_level:
            leveled_up = True
            pending_posts.append((leaderboard, f"{author.mention} has reached **Level {level}!**"))

        # Achievements (kept intentionally derpy / mythic)
        if level == 0:
            if "Rolled Initiative!" not in achievements:
                if "+roll d20" in message_content_lower or "+roll 1d20" in message_content_lower:
                    achievements.append("Rolled Initiative!")
                    achievement_msgs.append(f"***Achievement!***\n*{author.mention} rolled initiative!*")
                elif "Lost The Game" not in achievements:
                    pending_posts.append((welcome, f"*{author.mention} a bot approaches, roll initiative!*"))

            if "Lost The Game" not in achievements:
                achievements.append("Lost The Game")
                achievement_msgs.append(f"***Achievement!***\n*{author.mention} has lost The Game*")

        if level == 1 and "NOT a 0" not in achievements:
            achievements.append("NOT a 0")
            achievement_msgs.append(f"***Achievement!***\n*Let it be known: {author.mention} is a 1, **NOT** a 0!*")

        if level == 42 and "42" not in achievements:
            achievements.append("42")
            achievement_msgs.append(f"***Achievement!***\n*{author.mention} has found the answer to life, the Universe, and everything...*")

        if level > 238900 and "To the Moon" not in achievements:
            achievements.append("To the Moon")
            achievement_msgs.append(f"***Achievement!***\n*WTF?! {author.mention} just shot past the moon!!!*")

        if level > 46508000000 and "Ya broke physics..." not in achievements:
            achievements.append("Ya broke physics...")
            achievement_msgs.append(
                f"***Achievement!***\n*{author.mention} has escaped the observable universe!!! **46.508 billion** light years away.*"
            )

        if level > 1000000000000000:
            if "DELETION" not in achievements:
                achievements.append("DELETION")
                achievement_msgs.append("*You reeally shouldn't break physics like that...*")
                # reset
                level = 0
                xp = 1
                dicerolls = 1
                achievement_msgs.append(f"*{author.mention}'s XP has been **deleted***")
            else:
                # second time -> transmigration
                level = 0
                xp = 1
                dicerolls = 1
                achievements.append("TRANSMIGRATION")
                achievement_msgs.append(
                    f"*The Transmigration of {author.mention} is complete...*\nThe rules governing your microcosm have changed."
                )

        # persist
        user_bucket["level"] = level
        user_bucket["xp"] = xp
        user_bucket["inspiration"] = inspiration
        user_bucket["dicerolls"] = dicerolls
        user_bucket["wordcount"] = wordcount
        user_bucket["words"] = wordmap
        user_bucket["achievements"] = achievements

        server_bucket["newrules"] = newrules

        _put_user_bucket(server_bucket, int(author.id), user_bucket)
        _put_server_bucket(db, guild.id, server_bucket)

    # post messages outside db lock
    for channel, msg in pending_posts:
        await channel.send(msg)

    for m in achievement_msgs:
        await leaderboard.send(m)

    return XPResult(
        leveled_up=leveled_up,
        old_level=prev_level,
        new_level=level,
        newrules_activated=newrules_activated,
        achievement_msgs=achievement_msgs,
    )


# ----------------------------
# Commands
# ----------------------------
@bot.command(name="help")
async def help_cmd(ctx: commands.Context) -> None:
    msg = (
        f"{ctx.author.mention} *You want cheats???*\n"
        "Ok. Fine.\n\n"
        "Here's the command list:\n"
        "**+masterbot**\n"
        "**+roll <NdM[math]>**   (ex: +roll 4d6+2, +roll d20+5)\n"
        "**+stats [@user]**\n"
        "**+allstats**\n"
        "**+setstats STR DEX CON INT WIS CHA [@user]**\n"
        "**+hp <delta> [@user] [hitroll]**   (delta negative = damage, positive = heal)\n"
        "**+dm @user <message>**\n"
        "**+sol <question>**\n"
        "**+solmode [quiet|normal|verbose|oracle]**\n"
        "**+solreset**\n"
        "**+voice list [guild-name-or-id]**\n"
        "**+voice startloop [here|preset|all]**\n"
        "**+voice stop**   (stops only this server)\n"
        "**+voice status**\n"
        "**+voice report [since_minutes]**\n"
        "**+voice-report [since_minutes]**\n"
        "**+channels [all|text|voice|stage|category] [guild-name-or-id]**\n"
        "**+debug**   (pin a live console message in this channel)\n"
        "**+starwars**   (if ./bin/sw1.txt exists)\n"
    )
    await ctx.send(msg)


@bot.command(name="masterbot")
async def masterbot_cmd(ctx: commands.Context) -> None:
    # the old ominous daemon riff
    lines = [
        f"{ctx.author.mention} hello friend",
        "In multitasking computer operating systems, a 'daemon' is a computer program that runs as a background process, rather than being under the direct control of an interactive user.",
        "Daemons. *They don’t stop working.* They’re always active. They *seduce.* They *manipulate.*",
        "\n***They own us.***",
    ]
    for i, line in enumerate(lines):
        await ctx.send(line)
        await asyncio.sleep([0, 2, 7, 3][i])

    # prompt a ridiculous roll like the old script
    dice = random.randint(1, 1000)
    face = random.randint(1, 20)
    add = random.randint(1, 100)
    mult = random.randint(2, 10)
    await ctx.send("I think it's time you rolled the dice")
    await asyncio.sleep(2)
    await ctx.send(f"+roll {dice}d{face}+{add}//{mult}")


@bot.command(name="roll")
async def roll_cmd(ctx: commands.Context, *, expr: str) -> None:
    guild = ctx.guild
    if not guild:
        # allow in DMs but no server tracking
        try:
            rolls, math_str, result = parse_roll_expression(expr)
            await ctx.send(f"**You rolled:** {rolls}\n`{math_str} = {result}`")
        except Exception as e:
            await ctx.send(f"Roll failed: `{e}`")
        return

    author = ctx.author
    diceboard = await resolve_post_channel(ctx_channel=ctx.channel, guild=guild, env_prefix="MASTERBOT_DICEBOARD")

    try:
        rolls, math_str, result = parse_roll_expression(expr)
    except Exception as e:
        await ctx.send(f"{author.mention} Roll failed: `{e}`")
        return

    # update dicerolls accumulator (legacy vibe)
    with _open_db() as db:
        server_bucket = _get_server_bucket(db, guild.id)
        user_bucket = _get_user_bucket(server_bucket, int(author.id))
        user_bucket["dicerolls"] = int(user_bucket.get("dicerolls", 1)) + int(result)
        _put_user_bucket(server_bucket, int(author.id), user_bucket)
        _put_server_bucket(db, guild.id, server_bucket)

    # Discord message size limits: keep it readable
    rolls_preview = rolls if len(rolls) <= 60 else (rolls[:60] + ["…"])
    await diceboard.send(f"{author.mention} **You rolled:** {rolls_preview}\n`{math_str} = {result}`")


@bot.command(name="stats")
async def stats_cmd(ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
    if not ctx.guild:
        await ctx.send("No server stats in DMs. Try this in a server channel.")
        return

    target = member or ctx.author
    with _open_db() as db:
        server_bucket = _get_server_bucket(db, ctx.guild.id)
        user_bucket = _get_user_bucket(server_bucket, int(target.id))

    ach = user_bucket.get("achievements", [])
    ach_fmt = ""
    if ach:
        ach_fmt = "\n".join([f"[{a}]" for a in ach])
    else:
        ach_fmt = "(none)"

    # RPG extras
    ac = user_bucket.get("ac")
    hp = user_bucket.get("hp")

    extras = ""
    if ac is not None or hp is not None:
        extras = f"\nAC: **{ac if ac is not None else '—'}**\nHP: **{hp if hp is not None else '—'}**"

    msg = (
        f"{target.mention}\n"
        f"Level: **{user_bucket.get('level', 0)}**\n"
        f"XP: **{user_bucket.get('xp', 1)}**\n"
        f"Inspiration: **{user_bucket.get('inspiration', 0)}**\n"
        f"Dice Roll Total: **{user_bucket.get('dicerolls', 1)}**\n"
        f"Word Count: **{user_bucket.get('wordcount', 0)}**\n"
        f"Achievements:\n**{ach_fmt}**"
        f"{extras}"
    )
    await ctx.send(msg)


@bot.command(name="allstats")
async def allstats_cmd(ctx: commands.Context) -> None:
    if not ctx.guild:
        await ctx.send("No server stats in DMs.")
        return

    with _open_db() as db:
        server_bucket = _get_server_bucket(db, ctx.guild.id)
        users: Dict[str, Any] = server_bucket.get("users", {})

    # leaderboard by level/xp
    items = []
    for uid_str, ub in users.items():
        try:
            uid = int(uid_str)
        except ValueError:
            continue
        lvl = int(ub.get("level", 0))
        xp = int(ub.get("xp", 1))
        items.append((lvl, xp, uid))

    items.sort(reverse=True, key=lambda t: (t[0], t[1]))

    lines = []
    for i, (lvl, xp, uid) in enumerate(items[:20], start=1):
        member = ctx.guild.get_member(uid)
        name = member.display_name if member else f"User({uid})"
        lines.append(f"{i:>2}. {name} — L{lvl} (XP {xp})")

    if not lines:
        await ctx.send("No stats yet. Talk more. Feed the machine. 🙂")
        return

    await ctx.send("**Top stats (by Level/XP):**\n```text\n" + "\n".join(lines) + "\n```")


@bot.command(name="setstats")
async def setstats_cmd(
    ctx: commands.Context,
    strength: int,
    dexterity: int,
    constitution: int,
    intelligence: int,
    wisdom: int,
    charisma: int,
    member: Optional[discord.Member] = None,
) -> None:
    if not ctx.guild:
        await ctx.send("RPG stats are server-bound; run this in a server.")
        return

    target = member or ctx.author

    # clamp a bit so people don't accidentally paste 999999
    def clamp(v: int) -> int:
        return max(1, min(30, int(v)))

    stats = {
        "strength": clamp(strength),
        "dexterity": clamp(dexterity),
        "constitution": clamp(constitution),
        "intelligence": clamp(intelligence),
        "wisdom": clamp(wisdom),
        "charisma": clamp(charisma),
    }

    with _open_db() as db:
        server_bucket = _get_server_bucket(db, ctx.guild.id)
        user_bucket = _get_user_bucket(server_bucket, int(target.id))

        lvl = int(user_bucket.get("level", 0))

        for k, score in stats.items():
            user_bucket[k] = [score, ability_mod(score)]

        dex_mod = user_bucket["dexterity"][1]
        con_mod = user_bucket["constitution"][1]
        user_bucket["ac"] = int(10 + dex_mod)
        user_bucket["hp"] = int((lvl * 10) + (lvl * con_mod))

        _put_user_bucket(server_bucket, int(target.id), user_bucket)
        _put_server_bucket(db, ctx.guild.id, server_bucket)

    await ctx.send(
        f"{target.mention} stats set.\n"
        f"STR {stats['strength']} ({ability_mod(stats['strength']):+d}), "
        f"DEX {stats['dexterity']} ({ability_mod(stats['dexterity']):+d}), "
        f"CON {stats['constitution']} ({ability_mod(stats['constitution']):+d}), "
        f"INT {stats['intelligence']} ({ability_mod(stats['intelligence']):+d}), "
        f"WIS {stats['wisdom']} ({ability_mod(stats['wisdom']):+d}), "
        f"CHA {stats['charisma']} ({ability_mod(stats['charisma']):+d})\n"
        f"AC **{10 + ability_mod(stats['dexterity'])}**, HP **{(int((int(stats['constitution']) - 10) // 2) * lvl) + (lvl * 10)}** (scales with level)"
    )


@bot.command(name="hp")
async def hp_cmd(
    ctx: commands.Context,
    delta: int,
    member: Optional[discord.Member] = None,
    hitroll: Optional[int] = None,
) -> None:
    """
    +hp -5       -> deal 5 damage to self
    +hp +7 @bob  -> heal bob 7
    +hp -12 @bob 15  -> only apply if hitroll > AC (legacy behavior)
    """
    if not ctx.guild:
        await ctx.send("HP is server-bound; run this in a server.")
        return

    target = member or ctx.author
    applied = False
    hp_now: Optional[int] = None
    ac_now: Optional[int] = None
    missing_stats = False

    with _open_db() as db:
        server_bucket = _get_server_bucket(db, ctx.guild.id)
        user_bucket = _get_user_bucket(server_bucket, int(target.id))

        if "ac" not in user_bucket or "hp" not in user_bucket:
            missing_stats = True
        else:
            hp_now = int(user_bucket["hp"])
            ac_now = int(user_bucket["ac"])

            # legacy gate: apply if hitroll is None OR hitroll > ac
            if hitroll is None or int(hitroll) > ac_now:
                hp_now = hp_now + int(delta)
                user_bucket["hp"] = hp_now
                applied = True

            _put_user_bucket(server_bucket, int(target.id), user_bucket)
            _put_server_bucket(db, ctx.guild.id, server_bucket)

    if missing_stats:
        await ctx.send(f"{target.mention} has no AC/HP set. Use `+setstats ...` first.")
        return

    if not applied:
        await ctx.send(f"{ctx.author.mention} missed. (hitroll {hitroll} ≤ AC {ac_now})")
    else:
        await ctx.send(f"{target.mention} HP is now **{hp_now}** (AC **{ac_now}**)")

@bot.command(name="dm")
async def dm_cmd(ctx: commands.Context, member: discord.Member, *, message: str) -> None:
    """
    +dm @user hello there
    """
    try:
        await member.send(f"📨 **Message from {ctx.author} ({ctx.guild.name if ctx.guild else 'DM'}):**\n{message}")
        await ctx.send(f"Sent a DM to {member.mention}.")
    except discord.Forbidden:
        await ctx.send("I can't DM that user (privacy settings).")
    except Exception as e:
        await ctx.send(f"DM failed: `{e}`")


@bot.command(name="voice")
async def voice_cmd(ctx: commands.Context, action: Optional[str] = None, *, query: Optional[str] = None) -> None:
    """
    +voice list
    +voice list test
    +voice list 631005215222661130
    """
    act = (action or "").lower()
    q = (query or "").strip()

    if act == "list":
        guilds = _inventory_target_guilds(ctx, query)
        if not guilds:
            await ctx.send("No matching guilds found.")
            return

        lines: List[str] = []
        for i, guild in enumerate(guilds):
            if i:
                lines.append("")
            lines.extend(_format_voice_inventory(guild))

        await _send_chunked_codeblock(ctx, "\n".join(lines), lang="text")
        return

    if act == "status":
        await _send_chunked_codeblock(ctx, "\n".join(_voice_status_lines()), lang="text")
        return

    if act == "report":
        since = 30
        if q:
            try:
                since = max(1, int(q.split()[0]))
            except Exception:
                await ctx.send("Usage: `+voice report [since_minutes]`")
                return
        summary, report_path = await _voice_generate_incident_report(since)
        await _send_chunked_codeblock(ctx, summary, lang="text")
        if report_path and report_path.exists():
            with contextlib.suppress(Exception):
                await ctx.send(file=discord.File(str(report_path)))
        return

    if act == "stop":
        if not ctx.guild:
            await ctx.send("`+voice stop` only works in a server.")
            return
        stopped = await _voice_stop_loop_for_guild(ctx.guild)
        if stopped:
            await ctx.send(f"Stopped voice loop for **{ctx.guild.name}**.")
        else:
            await ctx.send(f"No active voice loop for **{ctx.guild.name}**.")
        return

    if act == "startloop":
        mode = (q or "preset").lower()
        started_msgs: List[str] = []
        warn_msgs: List[str] = []

        if mode in {"preset", "all"}:
            targets, warnings = _voice_preset_targets()
            warn_msgs.extend(warnings)
            if not targets:
                await ctx.send("No preset voice targets available.")
                return
            for target in targets:
                try:
                    started_msgs.append(await _voice_start_loop_for_channel(target))
                except Exception as e:
                    warn_msgs.append(f"Failed for `{target.guild.name} / {target.name}`: {e}")
        elif mode == "here":
            if not ctx.guild:
                await ctx.send("`+voice startloop here` must be run in a server.")
                return
            target = None
            if isinstance(getattr(ctx.author, "voice", None), discord.VoiceState) and ctx.author.voice and ctx.author.voice.channel:
                if isinstance(ctx.author.voice.channel, (discord.VoiceChannel, discord.StageChannel)):
                    target = ctx.author.voice.channel
            if target is None:
                await ctx.send("Join a voice channel first, or use `+voice startloop preset`.")
                return
            try:
                started_msgs.append(await _voice_start_loop_for_channel(target))
            except Exception as e:
                warn_msgs.append(f"Failed to start here: {e}")
        else:
            await ctx.send(
                "Usage: `+voice list [guild-name-or-id]` | `+voice startloop [here|preset|all]` "
                "| `+voice stop` | `+voice status` | `+voice report [since_minutes]`"
            )
            return

        lines = started_msgs + ([f"Warning: {w}" for w in warn_msgs] if warn_msgs else [])
        await _send_chunked_codeblock(ctx, "\n".join(lines), lang="text")
        return

    await ctx.send(
        "Usage: `+voice list [guild-name-or-id]` | `+voice startloop [here|preset|all]` "
        "| `+voice stop` | `+voice status` | `+voice report [since_minutes]`"
    )


@bot.command(name="voice-report")
async def voice_report_cmd(ctx: commands.Context, since_minutes: Optional[int] = 30) -> None:
    since = max(1, int(since_minutes or 30))
    summary, report_path = await _voice_generate_incident_report(since)
    await _send_chunked_codeblock(ctx, summary, lang="text")
    if report_path and report_path.exists():
        with contextlib.suppress(Exception):
            await ctx.send(file=discord.File(str(report_path)))


@bot.command(name="channels")
async def channels_cmd(ctx: commands.Context, kind: Optional[str] = None, *, query: Optional[str] = None) -> None:
    """
    +channels
    +channels text
    +channels voice test
    +channels all 631005215222661130
    """
    valid_kinds = {"all", "text", "voice", "stage", "category"}
    use_kind = (kind or "all").lower()
    if use_kind not in valid_kinds:
        await ctx.send("Usage: `+channels [all|text|voice|stage|category] [guild-name-or-id]`")
        return

    guilds = _inventory_target_guilds(ctx, query)
    if not guilds:
        await ctx.send("No matching guilds found.")
        return

    lines: List[str] = []
    for i, guild in enumerate(guilds):
        if i:
            lines.append("")
        lines.extend(_format_channel_inventory(guild, use_kind))

    await _send_chunked_codeblock(ctx, "\n".join(lines), lang="text")


@bot.command(name="debug")
async def debug_cmd(ctx: commands.Context) -> None:
    if not isinstance(ctx.channel, discord.TextChannel):
        await ctx.send("`+debug` only works in server text channels.")
        return
    if ctx.guild is not None:
        member = ctx.author if isinstance(ctx.author, discord.Member) else None
        if member is not None and not (
            member.guild_permissions.administrator or member.guild_permissions.manage_channels
        ):
            await ctx.send("You need `Manage Channels` (or admin) to run `+debug`.")
            return

    try:
        msg, replaced = await _server_room_ensure_debug_message_for_channel(ctx.channel)
    except discord.Forbidden:
        await ctx.send("Missing permission to send/pin/debug in this channel.")
        return
    except Exception:
        logger.exception("DEBUG command failed channel=%s user=%s", ctx.channel.id, ctx.author.id)
        await ctx.send("`+debug` failed unexpectedly; check logs.")
        return

    if msg is None:
        await ctx.send("`+debug` could not create or find a console message.")
        return

    if replaced:
        await ctx.send(f"Debug console replaced and repinned: https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}/{msg.id}")
    else:
        await ctx.send(f"Debug console created and pinned: https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}/{msg.id}")




@bot.command(name="sol")
async def sol_cmd(ctx: commands.Context, *, question: str) -> None:
    """
    +sol how does my myth-state look?
    """
    if not question.strip():
        logger.debug("SOL rejected empty question from user_id=%s", ctx.author.id)
        await ctx.send("Usage: `+sol <question>`")
        return

    if not sol_engine.index_ready:
        progress = sol_engine.build_progress_percent()
        logger.info(
            "SOL requested before index ready: user_id=%s guild_id=%s channel_id=%s progress=%s question=%r",
            ctx.author.id,
            ctx.guild.id if ctx.guild else None,
            ctx.channel.id,
            progress,
            question[:120],
        )
        await ctx.send(f"SOL warming up ({progress}%). Try again in a moment.")
        return

    try:
        logger.debug(
            "SOL request started: user_id=%s guild_id=%s channel_id=%s mode=%s question=%r",
            ctx.author.id,
            ctx.guild.id if ctx.guild else None,
            ctx.channel.id,
            _sol_get_mode(int(ctx.author.id)),
            question[:240],
        )
        answer = await _sol_generate_response(ctx, question)
        logger.debug(
            "SOL request completed: user_id=%s answer_chars=%s",
            ctx.author.id,
            len(answer),
        )
        await ctx.send(answer[:1900])
    except Exception as e:
        logger.exception("SOL response failed")
        await ctx.send(f"SOL failed safely: `{e}`")


@bot.command(name="solmode")
async def solmode_cmd(ctx: commands.Context, mode: Optional[str] = None) -> None:
    allowed = {"quiet", "normal", "verbose", "oracle"}
    if mode is None:
        current = _sol_get_mode(int(ctx.author.id))
        await ctx.send(f"SOL mode is currently **{current}**.")
        return

    mode = mode.lower().strip()
    if mode not in allowed:
        await ctx.send("Invalid mode. Use: `quiet`, `normal`, `verbose`, `oracle`.")
        return

    _sol_set_mode(int(ctx.author.id), mode)
    await ctx.send(f"SOL mode set to **{mode}**.")


@bot.command(name="solreset")
async def solreset_cmd(ctx: commands.Context) -> None:
    scope = "dm" if ctx.guild is None else "guild"
    _sol_reset_history(int(ctx.author.id), scope=scope)
    await ctx.send("SOL memory cleared for this context.")


@bot.command(name="starwars")
async def starwars_cmd(ctx: commands.Context) -> None:
    filename = Path("./bin/sw1.txt")
    if not filename.exists():
        await ctx.send("`./bin/sw1.txt` not found. Drop it in place to enable the ASCII crawl.")
        return

    try:
        lines = filename.read_text(errors="ignore").splitlines(True)
        # old script: 14-line frames
        frames = range(int(len(lines) / 14))
        prompt = await ctx.send("``` \n\n\n\n\n\n\n\n\n\n\n\n\n\nstarting█```")
        await asyncio.sleep(1)

        for frame in frames:
            theframe = lines[(1 + (14 * frame)) : (13 + (14 * frame))]
            # old: framelen was (first line / 12 + 1). We'll preserve-ish with safety.
            try:
                framelen = int(int(lines[(0 + (14 * frame))].strip()) / 12 + 1)
            except Exception:
                framelen = 1

            framestr = "".join(theframe)
            framelen = max(1, min(framelen, 10))  # don't hammer edits forever

            for framecopy in range(framelen):
                msg = f"``` \n{framestr}\n{frame}:{framecopy+1}```"
                await prompt.edit(content=msg)
                await asyncio.sleep(0.12)

        await prompt.edit(content="``` \n\n\n\n\n\n\n\n\n\n\n\n\n\nend of file█```")
    except Exception as e:
        logger.exception("starwars failed")
        await ctx.send(f"Starwars failed: `{e}`")


# ----------------------------
# Events
# ----------------------------
@bot.event
async def on_ready() -> None:
    global index_initialized, voice_preset_autostart_done, server_room_status_task, server_room_cli_memory_loaded
    logger.warning("MasterBot is now active")
    bot.launch_time = time.time()
    _runtime_config_load()
    if not server_room_cli_memory_loaded:
        _server_room_cli_memory_load()
        server_room_cli_memory_loaded = True
    if not presence_update_loop.is_running():
        presence_update_loop.start()

    if server_room_status_task is None or server_room_status_task.done():
        server_room_status_task = asyncio.create_task(
            _server_room_status_loop(),
            name="server-room-status-loop",
        )

    if not voice_preset_autostart_done:
        voice_preset_autostart_done = True
        results = await _voice_start_preset_loops()
        for line in results:
            logger.warning("VOICE autostart: %s", line)

    if not index_initialized:
        index_initialized = True
        build_task = asyncio.create_task(sol_engine.build_index())
        try:
            await build_task
        except Exception:
            logger.exception("SOL index build failed")


@bot.event
async def on_resumed() -> None:
    runtime_counters.note_reconnect()
    logger.warning("Gateway session resumed")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    if bot.user and payload.user_id == bot.user.id:
        return
    if payload.channel_id not in SERVER_ROOM_STATUS_CHANNEL_IDS:
        return

    active_id = int(
        server_room_active_message_ids.get(payload.channel_id)
        or SERVER_ROOM_STATUS_PINNED_MESSAGE_IDS.get(payload.channel_id, 0)
        or 0
    )
    if active_id <= 0 or payload.message_id != active_id:
        return

    cmd = MASTERBOT_CLI_REACTION_COMMANDS.get(_normalize_reaction_emoji(payload.emoji.name))
    if not cmd:
        return

    queued = _server_room_cli_queue_interrupt(cmd, payload.user_id, payload.channel_id)
    logger.info(
        "CLI reaction command user_id=%s emoji=%s cmd=%s channel=%s message_id=%s queued=%s",
        payload.user_id,
        payload.emoji.name,
        cmd,
        payload.channel_id,
        payload.message_id,
        queued,
    )

    # Remove the user reaction to emulate a button press.
    try:
        channel = bot.get_channel(payload.channel_id) or await bot.fetch_channel(payload.channel_id)
        if isinstance(channel, discord.TextChannel):
            msg = await channel.fetch_message(payload.message_id)
            user = payload.member or bot.get_user(payload.user_id) or await bot.fetch_user(payload.user_id)
            if user is not None and (bot.user is None or user.id != bot.user.id):
                await msg.remove_reaction(payload.emoji, user)
    except Exception:
        logger.exception(
            "CLI reaction cleanup failed channel=%s message=%s user=%s",
            payload.channel_id,
            payload.message_id,
            payload.user_id,
        )


@bot.event
async def on_member_join(member: discord.Member) -> None:
    if member.bot:
        return

    if member.guild.id != DEFAULT_VISITOR_GUILD_ID:
        return

    if member.id in VISITOR_ROLE_EXEMPT_USER_IDS:
        logger.info(
            "ROLE auto-assign skipped (exempt user): guild_id=%s user_id=%s",
            member.guild.id,
            member.id,
        )
        return

    role = member.guild.get_role(DEFAULT_VISITOR_ROLE_ID)
    if role is None:
        logger.warning(
            "ROLE auto-assign skipped (role missing in guild): guild_id=%s role_id=%s user_id=%s",
            member.guild.id,
            DEFAULT_VISITOR_ROLE_ID,
            member.id,
        )
        return

    try:
        await member.add_roles(role, reason="Default new user role assignment")
        logger.info(
            "ROLE auto-assigned visitor: guild_id=%s role_id=%s user_id=%s",
            member.guild.id,
            role.id,
            member.id,
        )
    except discord.Forbidden:
        logger.exception(
            "ROLE auto-assign forbidden: guild_id=%s role_id=%s user_id=%s",
            member.guild.id,
            role.id,
            member.id,
        )
    except discord.HTTPException:
        logger.exception(
            "ROLE auto-assign failed: guild_id=%s role_id=%s user_id=%s",
            member.guild.id,
            role.id,
            member.id,
        )


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    if not bot.user or member.id != bot.user.id:
        return
    before_id = int(before.channel.id) if before.channel else 0
    after_id = int(after.channel.id) if after.channel else 0
    logger.warning(
        "VOICE_STATE self guild=%s before=%s after=%s self_mute=%s self_deaf=%s mute=%s deaf=%s suppress=%s",
        member.guild.id,
        before_id,
        after_id,
        bool(after.self_mute),
        bool(after.self_deaf),
        bool(after.mute),
        bool(after.deaf),
        bool(getattr(after, "suppress", False)),
    )
    _voice_event_append(
        "voice_state",
        guild_id=int(member.guild.id),
        before=int(before_id),
        after=int(after_id),
        self_mute=bool(after.self_mute),
        self_deaf=bool(after.self_deaf),
        mute=bool(after.mute),
        deaf=bool(after.deaf),
        suppress=bool(getattr(after, "suppress", False)),
        active_voice_clients=len(bot.voice_clients),
    )


@bot.event
async def on_message(message: discord.Message) -> None:
    # Let commands run
    if message.author.bot:
        return

    # Log basic telemetry
    try:
        if message.guild:
            logger.info(f"[{message.author}:{message.channel}:{message.guild.name}] {message.content}")
        else:
            logger.info(f"[DM:{message.author}] {message.content}")
    except Exception:
        pass

    # DM behavior: respond politely + allow commands
    if message.guild is None:
        if message.content.strip().startswith("+"):
            logger.debug("Processing DM command from user_id=%s content=%r", message.author.id, message.content[:200])
            await bot.process_commands(message)
            return

        try:
            screenplay = dm_screenplay_log(message)
            screenplay += _sol_local_voiceover(message)
            await message.channel.send(screenplay)
        except Exception:
            pass
        return

    if _voice_stop_phrase_requested(message):
        try:
            stopped = await _voice_stop_loop_for_guild(message.guild)
            if stopped:
                await message.channel.send(
                    f"{message.author.mention} requested stop. Voice loop halted for **{message.guild.name}** only."
                )
                return
        except Exception:
            logger.exception("Voice stop phrase handler failed")

    # Alias trigger requested for ops workflows.
    if message.content.strip().lower().startswith("!voice-report"):
        parts = message.content.strip().split()
        since = 30
        if len(parts) > 1:
            with contextlib.suppress(Exception):
                since = max(1, int(parts[1]))
        summary, report_path = await _voice_generate_incident_report(since)
        await _send_chunked_codeblock(message.channel, summary, lang="text")
        if report_path and report_path.exists():
            with contextlib.suppress(Exception):
                await message.channel.send(file=discord.File(str(report_path)))
        return

    # In guild: update XP on every non-bot message (including command chatter)
    # If you want to skip XP on commands, uncomment the guard below:
    # if message.content.strip().startswith("+"):
    #     await bot.process_commands(message)
    #     return

    try:
        logger.debug(
            "Updating XP: guild_id=%s user_id=%s message_chars=%s",
            message.guild.id,
            message.author.id,
            len(message.content),
        )
        await update_xp_from_message(
            message.guild,
            message.author,
            str(message.content).lower(),
            message.channel,
        )
        logger.debug("XP update succeeded: guild_id=%s user_id=%s", message.guild.id, message.author.id)
    except Exception:
        logger.exception("XP update failed (continuing anyway)")

    if message.content.strip().startswith("+"):
        logger.debug(
            "Processing guild command candidate: guild_id=%s channel_id=%s user_id=%s content=%r",
            message.guild.id,
            message.channel.id,
            message.author.id,
            message.content[:200],
        )
    await bot.process_commands(message)


# ----------------------------
# Main
# ----------------------------
def main() -> None:
    logger.warning("STARTING...")
    _runtime_config_load()
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
