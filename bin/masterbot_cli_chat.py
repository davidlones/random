#!/usr/bin/env python3
"""Standalone CLI SOL chat runtime based on MasterBot's LLM behavior."""

from __future__ import annotations

import argparse
import atexit
import asyncio
import datetime
import hashlib
import json
import math
import os
import pickle
import re
import shelve
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from openai import OpenAI
from sol_ingest import SolIndex

SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

SOL_HISTORY_LIMIT = 20
DEFAULT_DB_PATH = LOG_DIR / "masterbot_cli_chat.db"
DEFAULT_CACHE_PATH = LOG_DIR / "sol_embeddings.pkl"
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
DEFAULT_LOCAL_MODEL_PATH = Path("~/.cache/models/Llama-3.2-1B-Instruct-Q4_K_M.gguf").expanduser()
ELEVEN_SPEAK_SCRIPT = PROJECT_ROOT / "bin" / "11speak.py"
HA_BASE_URL = os.getenv("HOME_ASSISTANT_BASE_URL", "https://ha.system42.one").strip()
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
    "apollo_co2": ["sensor.apollo_mtr_1_12022c_co2"],
    "apollo_pressure": ["sensor.apollo_mtr_1_12022c_dps310_pressure"],
    "apollo_dps_temp": ["sensor.apollo_mtr_1_12022c_dps310_temperature"],
    "apollo_esp_temp": ["sensor.apollo_mtr_1_12022c_esp_temperature"],
    "apollo_uv": ["sensor.apollo_mtr_1_12022c_ltr390_uv_index"],
    "apollo_rssi": ["sensor.apollo_mtr_1_12022c_rssi"],
    "apollo_uptime": ["sensor.apollo_mtr_1_12022c_uptime"],
    "apollo_online": ["binary_sensor.apollo_mtr_1_12022c_online"],
    "apollo_presence": ["sensor.apollo_mtr_1_12022c_presence_target_count"],
    "apollo_moving": ["sensor.apollo_mtr_1_12022c_moving_target_count"],
    "apollo_still": ["sensor.apollo_mtr_1_12022c_still_target_count"],
    "apollo_zone_1_all": ["sensor.apollo_mtr_1_12022c_zone_1_all_target_count"],
    "apollo_zone_2_all": ["sensor.apollo_mtr_1_12022c_zone_2_all_target_count"],
    "apollo_zone_3_all": ["sensor.apollo_mtr_1_12022c_zone_3_all_target_count"],
    "apollo_zone_1_moving": ["sensor.apollo_mtr_1_12022c_zone_1_moving_target_count"],
    "apollo_zone_2_moving": ["sensor.apollo_mtr_1_12022c_zone_2_moving_target_count"],
    "apollo_zone_3_moving": ["sensor.apollo_mtr_1_12022c_zone_3_moving_target_count"],
    "apollo_zone_1_still": ["sensor.apollo_mtr_1_12022c_zone_1_still_target_count"],
    "apollo_zone_2_still": ["sensor.apollo_mtr_1_12022c_zone_2_still_target_count"],
    "apollo_zone_3_still": ["sensor.apollo_mtr_1_12022c_zone_3_still_target_count"],
}
SESSION_LOG_EXCLUDE_GLOBS = [
    "codex_sessions/**",
    "sessionlogs/**",
    "session_logs/**",
    "**/sessionlog*.txt",
    "**/sessionlog*.jsonl",
    "**/sessionlog*.jsonl.txt",
]


@dataclass
class SessionState:
    start_ts: float
    turns: int = 0


@dataclass
class SpeakOptions:
    enabled: bool
    no_speaker: bool = False
    save_stream: str = ""
    telemetry_file: str = ""


def speak_with_11speak(text: str, opts: SpeakOptions) -> Tuple[bool, str]:
    if not opts.enabled:
        return (True, "")
    if not text.strip():
        return (True, "")
    if not ELEVEN_SPEAK_SCRIPT.exists():
        return (False, f"11speak script not found: {ELEVEN_SPEAK_SCRIPT}")

    cmd = ["python3", str(ELEVEN_SPEAK_SCRIPT), "-"]
    if opts.no_speaker:
        cmd.append("--no-speaker")
    if opts.save_stream:
        cmd.extend(["--save-stream", opts.save_stream])
    if opts.telemetry_file:
        cmd.extend(["--telemetry-file", opts.telemetry_file])

    try:
        proc = subprocess.run(
            cmd,
            input=text,
            text=True,
            capture_output=True,
            check=False,
            timeout=240,
        )
    except Exception as exc:
        return (False, str(exc))
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return (False, detail or f"11speak exited {proc.returncode}")
    return (True, "")


@dataclass(frozen=True)
class PresenceTelemetry:
    load_avg_1m: float
    memory_used_pct: Optional[float]
    disk_used_pct: float
    pending_updates: int
    active_ssh_sessions: int
    failed_login_attempts_24h: int


class TelemetrySnapshotProvider:
    def __init__(self, *, ha_base_url: str, refresh_s: float = 30.0) -> None:
        self.ha_base_url = ha_base_url.rstrip("/")
        self.refresh_s = max(5.0, float(refresh_s))
        self._lock = threading.Lock()
        self._collected_at = 0.0
        self._cached: Dict[str, Any] = {}

    @staticmethod
    def _read_system_uptime_s() -> Optional[int]:
        uptime_file = Path("/proc/uptime")
        if not uptime_file.exists():
            return None
        try:
            first = uptime_file.read_text(encoding="utf-8", errors="ignore").split()[0]
            return int(float(first))
        except Exception:
            return None

    @staticmethod
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

    @staticmethod
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
                if not m:
                    continue
                val = m.group(1).strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                token = val.strip()
            return token
        except Exception:
            return ""

    def _ha_get_state(self, token: str, entity_id: str) -> Optional[Dict[str, str]]:
        try:
            req = urllib.request.Request(
                f"{self.ha_base_url}/api/states/{entity_id}",
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
        except Exception:
            return None

    def _ha_first_available(self, token: str, entity_ids: List[str]) -> Optional[Dict[str, str]]:
        for entity_id in entity_ids:
            state = self._ha_get_state(token, entity_id)
            if not state:
                continue
            st = state.get("state", "").lower()
            if st in {"unknown", "unavailable", ""}:
                continue
            return state
        return None

    def _collect_presence(self) -> PresenceTelemetry:
        load_avg_1m = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
        return PresenceTelemetry(
            load_avg_1m=round(load_avg_1m, 2),
            memory_used_pct=self._memory_used_pct(),
            disk_used_pct=self._disk_used_pct(),
            pending_updates=self._pending_updates(),
            active_ssh_sessions=self._active_ssh_sessions(),
            failed_login_attempts_24h=self._failed_login_attempts_24h(),
        )

    def _collect_sync(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "system_uptime_s": self._read_system_uptime_s(),
            "cpu_temp_c": self._read_cpu_temp_c(),
            "gpu_temp_c": self._read_gpu_temp_c(),
        }
        p = self._collect_presence()
        data["presence"] = {
            "load_avg_1m": p.load_avg_1m,
            "memory_used_pct": p.memory_used_pct,
            "disk_used_pct": p.disk_used_pct,
            "pending_updates": p.pending_updates,
            "active_ssh_sessions": p.active_ssh_sessions,
            "failed_login_attempts_24h": p.failed_login_attempts_24h,
        }

        token = self._ha_token()
        if not token:
            data["ha_error"] = "HOME_ASSISTANT_TOKEN missing"
            return data

        data["ha_ambient"] = self._ha_first_available(token, HA_SENSOR_PREFS["ambient_temp"])
        data["ha_particulate"] = self._ha_first_available(token, HA_SENSOR_PREFS["particulate"])
        data["ha_light"] = self._ha_first_available(token, HA_SENSOR_PREFS["ambient_light"])
        data["ha_apollo_co2"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_co2"])
        data["ha_apollo_pressure"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_pressure"])
        data["ha_apollo_dps_temp"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_dps_temp"])
        data["ha_apollo_esp_temp"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_esp_temp"])
        data["ha_apollo_uv"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_uv"])
        data["ha_apollo_rssi"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_rssi"])
        data["ha_apollo_uptime"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_uptime"])
        data["ha_apollo_online"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_online"])
        data["ha_apollo_presence"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_presence"])
        data["ha_apollo_moving"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_moving"])
        data["ha_apollo_still"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_still"])
        data["ha_apollo_zone_1_all"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_1_all"])
        data["ha_apollo_zone_2_all"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_2_all"])
        data["ha_apollo_zone_3_all"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_3_all"])
        data["ha_apollo_zone_1_moving"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_1_moving"])
        data["ha_apollo_zone_2_moving"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_2_moving"])
        data["ha_apollo_zone_3_moving"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_3_moving"])
        data["ha_apollo_zone_1_still"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_1_still"])
        data["ha_apollo_zone_2_still"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_2_still"])
        data["ha_apollo_zone_3_still"] = self._ha_first_available(token, HA_SENSOR_PREFS["apollo_zone_3_still"])
        return data

    def get_snapshot(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            if self._cached and (now - self._collected_at) < self.refresh_s:
                return dict(self._cached)
        data = self._collect_sync()
        with self._lock:
            self._cached = dict(data)
            self._collected_at = now
            return dict(self._cached)

class LocalModelRunner:
    def __init__(
        self,
        *,
        model_path: Path,
        n_ctx: int = 4096,
        n_threads: int = 0,
        n_gpu_layers: int = 0,
        temperature: float = 0.6,
        max_tokens: int = 900,
    ) -> None:
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.n_gpu_layers = n_gpu_layers
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._llama_server_bin = shutil.which("llama-server")
        self._proc: Optional[subprocess.Popen[Any]] = None
        self._api_key = "local-sol-key"
        self._host = "127.0.0.1"
        self._port = int(os.getenv("MASTERBOT_LOCAL_LLM_PORT", "8091"))
        self._base_url = f"http://{self._host}:{self._port}/v1"
        self._model_alias = "local-sol"

    def _wait_for_server(
        self,
        timeout_s: float = 30.0,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        deadline = time.time() + timeout_s
        url = f"http://{self._host}:{self._port}/v1/models"
        req = urllib.request.Request(
            url=url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            method="GET",
        )
        if on_status:
            on_status("waiting for local LLM server")
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(req, timeout=1.2) as resp:
                    if 200 <= int(getattr(resp, "status", 0)) < 300:
                        if on_status:
                            on_status("local LLM server ready")
                        return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError(f"Local llama-server failed to start at {url}")

    def _ensure_loaded(self, on_status: Optional[Callable[[str], None]] = None) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        if not self.model_path.exists():
            raise RuntimeError(f"Local model not found: {self.model_path}")
        if not self._llama_server_bin:
            raise RuntimeError("Local backend requires `llama-server` in PATH.")

        cmd = [
            self._llama_server_bin,
            "-m",
            str(self.model_path),
            "--host",
            self._host,
            "--port",
            str(self._port),
            "-a",
            self._model_alias,
            "--api-key",
            self._api_key,
            "-c",
            str(self.n_ctx),
        ]
        if self.n_threads > 0:
            cmd.extend(["-t", str(self.n_threads)])
        if self.n_gpu_layers > 0:
            cmd.extend(["-ngl", str(self.n_gpu_layers)])

        if on_status:
            on_status("starting local LLM server")
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._wait_for_server(timeout_s=35.0, on_status=on_status)

    def generate(
        self,
        sys_text: str,
        style_text: str,
        input_text: str,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> str:
        self._ensure_loaded(on_status=on_status)
        client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        resp = client.chat.completions.create(
            model=self._model_alias,
            messages=[
                {"role": "system", "content": sys_text},
                {"role": "system", "content": style_text},
                {"role": "user", "content": input_text},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        content = resp.choices[0].message.content if resp.choices else ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [item.get("text", "") for item in content if isinstance(item, dict)]
            return "\n".join(x for x in parts if x).strip()
        return ""

    def generate_stream(
        self,
        sys_text: str,
        style_text: str,
        input_text: str,
        on_token,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> str:
        self._ensure_loaded(on_status=on_status)
        client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        stream = client.chat.completions.create(
            model=self._model_alias,
            messages=[
                {"role": "system", "content": sys_text},
                {"role": "system", "content": style_text},
                {"role": "user", "content": input_text},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        out: List[str] = []
        for event in stream:
            if not getattr(event, "choices", None):
                continue
            delta = event.choices[0].delta
            piece = getattr(delta, "content", None)
            if isinstance(piece, str) and piece:
                out.append(piece)
                on_token(piece)
            elif isinstance(piece, list):
                parts = [it.get("text", "") for it in piece if isinstance(it, dict)]
                text = "".join(parts)
                if text:
                    out.append(text)
                    on_token(text)
        return "".join(out).strip()

    def stop(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=3)
        self._proc = None


class CachedSolSearch:
    def __init__(
        self,
        *,
        client: Optional[OpenAI],
        cache_path: Path,
        embed_model: str,
        top_k: int,
        enabled: bool,
    ) -> None:
        self.client = client
        self.cache_path = cache_path
        self.embed_model = embed_model
        self.top_k = top_k
        self.enabled = enabled
        self.docs: List[Dict[str, Any]] = []
        self.embeddings: List[List[float]] = []
        self.loaded_at: float = 0.0

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(y * y for y in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def load(self) -> int:
        self.docs = []
        self.embeddings = []
        self.loaded_at = time.time()

        if not self.enabled or not self.cache_path.exists():
            return 0

        try:
            obj = pickle.loads(self.cache_path.read_bytes())
        except Exception:
            return 0

        if not isinstance(obj, dict):
            return 0

        cache_docs = obj.get("docs", {})
        if not isinstance(cache_docs, dict):
            return 0

        for entry in cache_docs.values():
            if not isinstance(entry, dict):
                continue
            items = entry.get("entries")
            if items is None:
                items = entry.get("chunks")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                doc = item.get("doc")
                emb = item.get("embedding")
                if not isinstance(doc, dict) or not isinstance(emb, list):
                    continue
                if "chunk_index" not in doc and "chunk" in doc:
                    doc["chunk_index"] = doc.get("chunk")
                if "chunk" not in doc and "chunk_index" in doc:
                    doc["chunk"] = doc.get("chunk_index")
                self.docs.append(doc)
                try:
                    self.embeddings.append([float(x) for x in emb])
                except Exception:
                    self.docs.pop()

        return len(self.docs)

    def ingest_directory(
        self,
        *,
        directory: Path,
        chunk_size: int,
        overlap: int,
        batch_size: int,
        ignore_session_log_dir: bool = False,
    ) -> int:
        if not self.enabled or self.client is None:
            return 0
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        directory = directory.expanduser().resolve()
        if not directory.exists() or not directory.is_dir():
            raise ValueError(f"ingest directory missing or not a directory: {directory}")
        dir_hash = hashlib.sha1(str(directory).encode("utf-8")).hexdigest()[:12]
        ingest_cache = self.cache_path.with_name(
            f"{self.cache_path.stem}.ingest.{dir_hash}{self.cache_path.suffix}"
        )
        idx = SolIndex(
            model=self.embed_model,
            backend="openai",
            embed_batch_size=batch_size,
            chunk_size=chunk_size,
            overlap=overlap,
            knowledge_dir=directory,
            cache_path=ingest_cache,
            exclude_globs=list(SESSION_LOG_EXCLUDE_GLOBS) if ignore_session_log_dir else [],
        )
        idx.build(quiet=True)
        appended = 0
        for doc, vec in zip(idx.docs, idx.embeddings):
            if "chunk_index" not in doc and "chunk" in doc:
                doc["chunk_index"] = doc.get("chunk")
            if "chunk" not in doc and "chunk_index" in doc:
                doc["chunk"] = doc.get("chunk_index")
            self.docs.append(doc)
            self.embeddings.append([float(x) for x in vec])
            appended += 1
        return appended

    async def search(self, query: str) -> List[Dict[str, Any]]:
        if not self.enabled or not self.docs or not self.embeddings or self.client is None:
            return []

        def _search_sync() -> List[Dict[str, Any]]:
            query_vec = list(
                self.client.embeddings.create(model=self.embed_model, input=query).data[0].embedding
            )
            scored: List[Tuple[float, Dict[str, Any]]] = []
            for doc, emb in zip(self.docs, self.embeddings):
                scored.append((self._cosine_similarity(query_vec, emb), doc))
            scored.sort(key=lambda x: x[0], reverse=True)
            out: List[Dict[str, Any]] = []
            for score, doc in scored[: self.top_k]:
                out.append({"score": score, **doc})
            return out

        return await asyncio.to_thread(_search_sync)


class SolCli:
    def __init__(
        self,
        *,
        user_id: str,
        db_path: Path,
        client: Optional[OpenAI],
        search: CachedSolSearch,
        model: str,
        scope: str,
        backend: str,
        local_runner: Optional[LocalModelRunner],
        telemetry_provider: TelemetrySnapshotProvider,
    ) -> None:
        self.user_id = user_id
        self.db_path = db_path
        self.client = client
        self.search = search
        self.model = model
        self.scope = scope
        self.backend = backend
        self.local_runner = local_runner
        self.telemetry_provider = telemetry_provider
        self.session = SessionState(start_ts=time.time())

    def _open_db(self) -> shelve.DbfilenameShelf:
        db = shelve.open(str(self.db_path), flag="c", writeback=False)
        if "sol_mode" not in db:
            db["sol_mode"] = {}
        if "sol_history_dm" not in db:
            db["sol_history_dm"] = {}
        if "sol_history_guild" not in db:
            db["sol_history_guild"] = {}
        return db

    def get_mode(self) -> str:
        with self._open_db() as db:
            modes = db.get("sol_mode", {})
            return str(modes.get(self.user_id, "normal"))

    def set_mode(self, mode: str) -> None:
        with self._open_db() as db:
            modes = db.get("sol_mode", {})
            modes[self.user_id] = mode
            db["sol_mode"] = modes

    def _history_key(self) -> str:
        return "sol_history_dm" if self.scope == "dm" else "sol_history_guild"

    def append_history(self, role: str, content: str) -> None:
        with self._open_db() as db:
            key = self._history_key()
            history = db.get(key, {})
            user_history = list(history.get(self.user_id, []))
            user_history.append({"role": role, "content": content[-1500:]})
            history[self.user_id] = user_history[-SOL_HISTORY_LIMIT:]
            db[key] = history

    def get_history(self) -> List[Dict[str, str]]:
        with self._open_db() as db:
            key = self._history_key()
            history = db.get(key, {})
            return list(history.get(self.user_id, []))[-SOL_HISTORY_LIMIT:]

    def reset_history(self) -> None:
        with self._open_db() as db:
            key = self._history_key()
            history = db.get(key, {})
            history.pop(self.user_id, None)
            db[key] = history

    def telemetry_snapshot(self) -> Dict[str, Any]:
        snapshot = self.telemetry_provider.get_snapshot()
        system_uptime = snapshot.get("system_uptime_s")
        return {
            "system_uptime_s": system_uptime,
            "session_uptime_s": int(time.time() - self.session.start_ts),
            "session_turns": self.session.turns,
            "hostname": socket.gethostname(),
            "backend": self.backend,
            "cpu_temp_c": snapshot.get("cpu_temp_c"),
            "gpu_temp_c": snapshot.get("gpu_temp_c"),
            "presence": snapshot.get("presence"),
            "ha_ambient": snapshot.get("ha_ambient"),
            "ha_particulate": snapshot.get("ha_particulate"),
            "ha_light": snapshot.get("ha_light"),
            "ha_apollo_co2": snapshot.get("ha_apollo_co2"),
            "ha_apollo_pressure": snapshot.get("ha_apollo_pressure"),
            "ha_apollo_dps_temp": snapshot.get("ha_apollo_dps_temp"),
            "ha_apollo_esp_temp": snapshot.get("ha_apollo_esp_temp"),
            "ha_apollo_uv": snapshot.get("ha_apollo_uv"),
            "ha_apollo_rssi": snapshot.get("ha_apollo_rssi"),
            "ha_apollo_uptime": snapshot.get("ha_apollo_uptime"),
            "ha_apollo_online": snapshot.get("ha_apollo_online"),
            "ha_apollo_presence": snapshot.get("ha_apollo_presence"),
            "ha_apollo_moving": snapshot.get("ha_apollo_moving"),
            "ha_apollo_still": snapshot.get("ha_apollo_still"),
            "ha_apollo_zone_1_all": snapshot.get("ha_apollo_zone_1_all"),
            "ha_apollo_zone_2_all": snapshot.get("ha_apollo_zone_2_all"),
            "ha_apollo_zone_3_all": snapshot.get("ha_apollo_zone_3_all"),
            "ha_apollo_zone_1_moving": snapshot.get("ha_apollo_zone_1_moving"),
            "ha_apollo_zone_2_moving": snapshot.get("ha_apollo_zone_2_moving"),
            "ha_apollo_zone_3_moving": snapshot.get("ha_apollo_zone_3_moving"),
            "ha_apollo_zone_1_still": snapshot.get("ha_apollo_zone_1_still"),
            "ha_apollo_zone_2_still": snapshot.get("ha_apollo_zone_2_still"),
            "ha_apollo_zone_3_still": snapshot.get("ha_apollo_zone_3_still"),
            "ha_error": snapshot.get("ha_error"),
        }

    def myth_state(self) -> Dict[str, Any]:
        return {
            "newrules": False,
            "level": 0,
            "xp": 1,
            "achievements": [],
            "transmigrated": False,
        }

    @staticmethod
    def _is_status_query(question: str) -> bool:
        q = question.lower()
        hints = [
            "systems status",
            "system status",
            "status summary",
            "telemetry",
            "health check",
            "uptime",
        ]
        return any(h in q for h in hints)

    @staticmethod
    def _sensor_value(payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        state = str(payload.get("state", "")).strip()
        unit = str(payload.get("unit", "")).strip()
        if not state:
            return None
        return f"{state} {unit}".strip()

    def _telemetry_compact_view(self, telemetry: Any) -> Dict[str, Any]:
        if not isinstance(telemetry, dict):
            return {}
        presence = telemetry.get("presence", {})
        if not isinstance(presence, dict):
            presence = {}
        return {
            "system_uptime_s": telemetry.get("system_uptime_s"),
            "session_uptime_s": telemetry.get("session_uptime_s"),
            "cpu_temp_c": telemetry.get("cpu_temp_c"),
            "gpu_temp_c": telemetry.get("gpu_temp_c"),
            "presence": {
                "load_avg_1m": presence.get("load_avg_1m"),
                "memory_used_pct": presence.get("memory_used_pct"),
                "disk_used_pct": presence.get("disk_used_pct"),
                "pending_updates": presence.get("pending_updates"),
                "active_ssh_sessions": presence.get("active_ssh_sessions"),
                "failed_login_attempts_24h": presence.get("failed_login_attempts_24h"),
            },
            "ambient_temp": self._sensor_value(telemetry.get("ha_ambient")),
            "pm25": self._sensor_value(telemetry.get("ha_particulate")),
            "ambient_light": self._sensor_value(telemetry.get("ha_light")),
            "apollo_online": self._sensor_value(telemetry.get("ha_apollo_online")),
            "apollo_co2": self._sensor_value(telemetry.get("ha_apollo_co2")),
            "apollo_pressure": self._sensor_value(telemetry.get("ha_apollo_pressure")),
            "apollo_rssi": self._sensor_value(telemetry.get("ha_apollo_rssi")),
            "ha_error": telemetry.get("ha_error"),
        }

    async def generate(
        self,
        question: str,
        *,
        include_mode: bool = True,
        include_myth: bool = True,
        include_telemetry: bool = True,
        include_telemetry_metadata: bool = False,
        include_history: bool = True,
        include_semantic: bool = True,
        persist_history: bool = True,
        stream_output: bool = False,
        stream_prefix: str = "",
        on_status: Optional[Callable[[str], None]] = None,
    ) -> str:
        def _status(msg: str) -> None:
            if on_status:
                on_status(msg)

        _status("preparing context")
        mode = self.get_mode()
        hist = self.get_history() if include_history else []
        myth = self.myth_state() if include_myth else None
        if include_telemetry:
            _status("collecting telemetry and sensors")
        telemetry = self.telemetry_snapshot() if include_telemetry else None

        if include_semantic:
            _status("running semantic search")
        matches = await self.search.search(question) if include_semantic else []
        snippets: List[str] = []
        for m in matches:
            text = str(m.get("text", "")).replace("\n", " ").strip()[:240]
            path = str(m.get("path", "?"))
            chunk = m.get("chunk_index", m.get("chunk", "?"))
            snippets.append(f"[{path}#{chunk}] {text}")

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
        style_text = mode_map.get(mode, mode_map["oracle"])
        if self._is_status_query(question):
            style_text += (
                " For system/status questions, prioritize concise value+state reporting. "
                "Use TelemetrySummary labels as the source of truth. "
                "Avoid verbose narrative and do not include entity_id/name/unit metadata unless explicitly asked. "
                "Keep output to short bullets."
            )

        input_lines: List[str] = [f"Question: {question}"]
        if include_mode:
            input_lines.insert(0, f"Mode: {mode}")
        if include_myth:
            input_lines.append(f"MythState: {myth}")
        if include_telemetry:
            input_lines.append(f"TelemetrySummary: {self._telemetry_compact_view(telemetry)}")
            if include_telemetry_metadata:
                input_lines.append(f"TelemetryMetadata: {telemetry}")
        if include_history:
            input_lines.append(f"RecentHistory: {hist[-8:]}")
        if include_semantic:
            input_lines.append(f"SemanticMatches: {snippets if snippets else ['(none)']}")
            input_lines.append(
                "When relevant, quote short snippets from SemanticMatches and mention their source labels."
            )
        if include_telemetry:
            input_lines.append(
                "Telemetry values are authoritative; do not claim 'unavailable' when a field has a concrete value."
            )
            input_lines.append(
                "If TelemetrySummary is present, prefer those labels and values in your answer."
            )
        input_text = "\n".join(input_lines)

        def _call_model() -> str:
            if self.backend == "local":
                if self.local_runner is None:
                    raise RuntimeError("Local backend is selected but not initialized.")
                if stream_output:
                    return self.local_runner.generate_stream(
                        sys_text,
                        style_text,
                        input_text,
                        _on_stream_token,
                        on_status=_status,
                    )
                return self.local_runner.generate(
                    sys_text,
                    style_text,
                    input_text,
                    on_status=_status,
                )

            if self.client is None:
                raise RuntimeError("OpenAI backend selected but OPENAI_API_KEY is not set.")

            if hasattr(self.client, "responses"):
                resp = self.client.responses.create(
                    model=self.model,
                    input=[
                        {"role": "system", "content": [{"type": "input_text", "text": sys_text}]},
                        {"role": "system", "content": [{"type": "input_text", "text": style_text}]},
                        {"role": "user", "content": [{"type": "input_text", "text": input_text}]},
                    ],
                    temperature=0.6,
                )
                return (resp.output_text or "").strip()

            resp = self.client.chat.completions.create(
                model=self.model,
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
                return "\n".join(x for x in parts if x).strip()
            return ""

        wrote_stream = False

        def _on_stream_token(token: str) -> None:
            nonlocal wrote_stream
            if not wrote_stream:
                if stream_prefix:
                    sys.stdout.write("\r" + stream_prefix)
                wrote_stream = True
            sys.stdout.write(token)
            sys.stdout.flush()

        def _call_model_openai_stream() -> str:
            if self.client is None:
                raise RuntimeError("OpenAI backend selected but OPENAI_API_KEY is not set.")
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": sys_text},
                    {"role": "system", "content": style_text},
                    {"role": "user", "content": input_text},
                ],
                temperature=0.6,
                stream=True,
            )
            out: List[str] = []
            for event in stream:
                if not getattr(event, "choices", None):
                    continue
                delta = event.choices[0].delta
                piece = getattr(delta, "content", None)
                if isinstance(piece, str) and piece:
                    out.append(piece)
                    _on_stream_token(piece)
                elif isinstance(piece, list):
                    parts = [it.get("text", "") for it in piece if isinstance(it, dict)]
                    text = "".join(parts)
                    if text:
                        out.append(text)
                        _on_stream_token(text)
            return "".join(out).strip()

        _status("generating response")
        if stream_output and self.backend == "openai":
            answer = await asyncio.to_thread(_call_model_openai_stream)
        else:
            answer = await asyncio.to_thread(_call_model)
        if stream_output:
            if not wrote_stream and stream_prefix:
                sys.stdout.write("\r" + stream_prefix + (answer or "(no output)"))
            sys.stdout.write("\n")
            sys.stdout.flush()
        if persist_history:
            self.append_history("user", question)
            self.append_history("assistant", answer)
        self.session.turns += 1
        return answer or "I have no stable answer yet. Try reframing your question."


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Standalone MasterBot-style SOL CLI chat")
    p.add_argument("--backend", choices=["openai", "local"], default="openai", help="LLM backend")
    p.add_argument("--user-id", default="local-user", help="Stable local user id for mode/history")
    p.add_argument("--scope", choices=["dm", "guild"], default="dm", help="History bucket scope")
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Shelve DB path for mode/history")
    p.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH), help="SOL embeddings cache path")
    p.add_argument(
        "--ignore-session-log-dir",
        action="store_true",
        help="Exclude session-log directories/files when ingesting --semantic-ingest-dir paths.",
    )
    p.add_argument(
        "--semantic-ingest-dir",
        action="append",
        default=[],
        help=(
            "Additional directory to chunk+embed for retrieval context. "
            "Repeatable; files with .txt/.md/.markdown are ingested."
        ),
    )
    p.add_argument(
        "--semantic-ingest-chunk-size",
        type=int,
        default=900,
        help="Chunk size used for --semantic-ingest-dir",
    )
    p.add_argument(
        "--semantic-ingest-overlap",
        type=int,
        default=120,
        help="Chunk overlap used for --semantic-ingest-dir",
    )
    p.add_argument(
        "--semantic-ingest-batch-size",
        type=int,
        default=64,
        help="Embedding batch size used for --semantic-ingest-dir",
    )
    p.add_argument("--model", default=DEFAULT_MODEL, help="LLM model for OpenAI backend")
    p.add_argument(
        "--local-model-path",
        default=str(DEFAULT_LOCAL_MODEL_PATH),
        help="Path to GGUF local model file used by --backend local",
    )
    p.add_argument("--local-n-ctx", type=int, default=4096, help="Context length for local backend")
    p.add_argument("--local-n-threads", type=int, default=0, help="CPU threads for local backend (0=auto)")
    p.add_argument("--local-n-gpu-layers", type=int, default=0, help="GPU layers for local backend")
    p.add_argument("--local-max-tokens", type=int, default=900, help="Max output tokens for local backend")
    p.add_argument(
        "--embed-model",
        default=DEFAULT_EMBED_MODEL,
        help="Embedding model for query vectors when retrieval is enabled",
    )
    p.add_argument("--top-k", type=int, default=4, help="Semantic snippets to include")
    p.add_argument("--ha-base-url", default=HA_BASE_URL, help="Home Assistant base URL")
    p.add_argument("--telemetry-refresh-s", type=float, default=30.0, help="Telemetry cache TTL in seconds")
    p.add_argument("--ctx-mode", action="store_true", help="Include mode in model context")
    p.add_argument("--ctx-myth", action="store_true", help="Include myth state in model context")
    p.add_argument("--ctx-telemetry", action="store_true", help="Include telemetry/sensors in model context")
    p.add_argument("--ctx-history", action="store_true", help="Include recent history in model context")
    p.add_argument("--ctx-semantic", action="store_true", help="Include semantic search snippets in model context")
    p.add_argument("--speak", action="store_true", help="Speak each response using 11speak")
    p.add_argument("--speak-no-speaker", action="store_true", help="Use 11speak without local playback")
    p.add_argument("--speak-save-stream", default="", help="Optional mp3 output path for 11speak")
    p.add_argument("--speak-telemetry-file", default="", help="Optional telemetry ndjson path for 11speak")
    p.add_argument("--no-stream", action="store_true", help="Disable streaming output (wait for full completion)")
    p.add_argument("--no-retrieval", action="store_true", help="Disable semantic retrieval")
    p.add_argument("--ask", default="", help="One-shot question; if omitted, starts REPL")
    p.add_argument("--show-prompt", action="store_true", help="Print startup settings")
    return p


async def run_repl(
    sol: SolCli,
    search: CachedSolSearch,
    *,
    include_mode: bool,
    include_myth: bool,
    include_telemetry: bool,
    include_history: bool,
    include_semantic: bool,
    stream_output: bool,
    speak_opts: SpeakOptions,
    semantic_ingest_dirs: List[Path],
    semantic_ingest_chunk_size: int,
    semantic_ingest_overlap: int,
    semantic_ingest_batch_size: int,
    ignore_session_log_dir: bool,
) -> int:
    def show_status(msg: str) -> None:
        print(f"\rsol> {msg}...", flush=True)

    print("SOL CLI ready. Commands: /help /mode [quiet|normal|verbose|oracle] /reset /history /reload-index /quit")
    while True:
        try:
            line = input("you> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 0

        if not line:
            continue

        if line == "/quit":
            return 0
        if line == "/help":
            print("/mode [quiet|normal|verbose|oracle]  set/view mode")
            print("/reset                               clear conversation memory in current scope")
            print("/history                             show recent memory")
            print("/reload-index                        reload semantic cache from disk")
            print("/quit                                exit")
            continue
        if line.startswith("/mode"):
            parts = line.split(maxsplit=1)
            if len(parts) == 1:
                print(f"mode> {sol.get_mode()}")
                continue
            mode = parts[1].strip().lower()
            if mode not in {"quiet", "normal", "verbose", "oracle"}:
                print("mode> invalid; choose quiet|normal|verbose|oracle")
                continue
            sol.set_mode(mode)
            print(f"mode> {mode}")
            continue
        if line == "/reset":
            sol.reset_history()
            print("memory> cleared")
            continue
        if line == "/history":
            hist = sol.get_history()
            print(json.dumps(hist, indent=2))
            continue
        if line == "/reload-index":
            count = search.load()
            for ingest_dir in semantic_ingest_dirs:
                try:
                    count += search.ingest_directory(
                        directory=ingest_dir,
                        chunk_size=semantic_ingest_chunk_size,
                        overlap=semantic_ingest_overlap,
                        batch_size=semantic_ingest_batch_size,
                        ignore_session_log_dir=ignore_session_log_dir,
                    )
                except Exception as exc:
                    print(f"retrieval> skipped ingest dir {ingest_dir}: {exc}")
            print(f"retrieval> loaded {count} chunks from {search.cache_path}")
            continue

        try:
            if stream_output:
                print("sol> thinking...", flush=True)
            answer = await sol.generate(
                line,
                include_mode=include_mode,
                include_myth=include_myth,
                include_telemetry=include_telemetry,
                include_history=include_history,
                include_semantic=include_semantic,
                persist_history=True,
                stream_output=stream_output,
                stream_prefix="sol> ",
                on_status=show_status,
            )
            if not stream_output:
                print(f"sol> {answer}")
            if speak_opts.enabled:
                print("sol> speaking response...", flush=True)
                ok, err = await asyncio.to_thread(speak_with_11speak, answer, speak_opts)
                if not ok:
                    print(f"warning> 11speak failed: {err}")
        except Exception as exc:
            print(f"error> {exc}")


def main() -> int:
    args = build_parser().parse_args()

    if args.top_k <= 0:
        print("Error: --top-k must be > 0")
        return 1
    if args.semantic_ingest_chunk_size <= 0:
        print("Error: --semantic-ingest-chunk-size must be > 0")
        return 1
    if args.semantic_ingest_overlap < 0:
        print("Error: --semantic-ingest-overlap must be >= 0")
        return 1
    if args.semantic_ingest_overlap >= args.semantic_ingest_chunk_size:
        print("Error: --semantic-ingest-overlap must be smaller than --semantic-ingest-chunk-size")
        return 1
    if args.semantic_ingest_batch_size <= 0:
        print("Error: --semantic-ingest-batch-size must be > 0")
        return 1

    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    if args.backend == "openai" and not has_openai:
        print("Error: OPENAI_API_KEY is not set for --backend openai.")
        return 1

    client: Optional[OpenAI] = OpenAI() if has_openai else None

    retrieval_enabled = not args.no_retrieval
    if retrieval_enabled and client is None:
        retrieval_enabled = False
        print("Warning: retrieval disabled because OPENAI_API_KEY is not set.")

    local_runner: Optional[LocalModelRunner] = None
    if args.backend == "local":
        local_runner = LocalModelRunner(
            model_path=Path(args.local_model_path).expanduser(),
            n_ctx=args.local_n_ctx,
            n_threads=args.local_n_threads,
            n_gpu_layers=args.local_n_gpu_layers,
            max_tokens=args.local_max_tokens,
        )
        atexit.register(local_runner.stop)

    search = CachedSolSearch(
        client=client,
        cache_path=Path(args.cache_path),
        embed_model=args.embed_model,
        top_k=args.top_k,
        enabled=retrieval_enabled,
    )
    loaded = search.load()
    semantic_ingest_dirs = [Path(p).expanduser() for p in (args.semantic_ingest_dir or [])]
    ingested_chunks = 0
    if retrieval_enabled and semantic_ingest_dirs:
        for ingest_dir in semantic_ingest_dirs:
            try:
                ingested_chunks += search.ingest_directory(
                    directory=ingest_dir,
                    chunk_size=args.semantic_ingest_chunk_size,
                    overlap=args.semantic_ingest_overlap,
                    batch_size=args.semantic_ingest_batch_size,
                    ignore_session_log_dir=bool(args.ignore_session_log_dir),
                )
            except Exception as exc:
                print(f"Warning: skipping --semantic-ingest-dir {ingest_dir}: {exc}")
    elif semantic_ingest_dirs:
        print("Warning: --semantic-ingest-dir ignored because retrieval is disabled.")
    include_mode = args.ctx_mode
    include_myth = args.ctx_myth
    include_telemetry = args.ctx_telemetry
    include_semantic = args.ctx_semantic
    include_history_repl = args.ctx_history
    include_history_ask = args.ctx_history
    stream_output = not args.no_stream
    speak_opts = SpeakOptions(
        enabled=bool(args.speak),
        no_speaker=bool(args.speak_no_speaker),
        save_stream=str(args.speak_save_stream or ""),
        telemetry_file=str(args.speak_telemetry_file or ""),
    )
    telemetry_provider = TelemetrySnapshotProvider(
        ha_base_url=args.ha_base_url,
        refresh_s=args.telemetry_refresh_s,
    )

    sol = SolCli(
        user_id=args.user_id,
        db_path=Path(args.db_path),
        client=client,
        search=search,
        model=args.model,
        scope=args.scope,
        backend=args.backend,
        local_runner=local_runner,
        telemetry_provider=telemetry_provider,
    )

    if args.show_prompt:
        print(
            json.dumps(
                {
                    "user_id": args.user_id,
                    "scope": args.scope,
                    "backend": args.backend,
                    "mode": sol.get_mode(),
                    "model": args.model,
                    "local_model_path": str(Path(args.local_model_path).expanduser()),
                    "retrieval_enabled": retrieval_enabled,
                    "retrieval_cache": args.cache_path,
                    "retrieval_chunks_loaded": loaded,
                    "retrieval_chunks_ingested": ingested_chunks,
                    "semantic_ingest_dirs": [str(x) for x in semantic_ingest_dirs],
                    "semantic_ingest_chunk_size": args.semantic_ingest_chunk_size,
                    "semantic_ingest_overlap": args.semantic_ingest_overlap,
                    "semantic_ingest_batch_size": args.semantic_ingest_batch_size,
                    "ignore_session_log_dir": bool(args.ignore_session_log_dir),
                    "ha_base_url": args.ha_base_url,
                    "telemetry_refresh_s": args.telemetry_refresh_s,
                    "ctx_mode": include_mode,
                    "ctx_myth": include_myth,
                    "ctx_telemetry": include_telemetry,
                    "ctx_history": include_history_ask if args.ask.strip() else include_history_repl,
                    "ctx_semantic": include_semantic,
                    "stream_output": stream_output,
                    "speak_enabled": speak_opts.enabled,
                    "speak_no_speaker": speak_opts.no_speaker,
                    "speak_save_stream": speak_opts.save_stream,
                    "speak_telemetry_file": speak_opts.telemetry_file,
                    "db_path": args.db_path,
                },
                indent=2,
            )
        )

    if args.ask.strip():
        def show_status(msg: str) -> None:
            print(f"sol> {msg}...", flush=True)

        try:
            answer = asyncio.run(
                sol.generate(
                    args.ask.strip(),
                    include_mode=include_mode,
                    include_myth=include_myth,
                    include_telemetry=include_telemetry,
                    include_history=include_history_ask,
                    include_semantic=include_semantic,
                    persist_history=False,
                    stream_output=stream_output,
                    stream_prefix="",
                    on_status=show_status,
                )
            )
        except Exception as exc:
            print(f"Error: {exc}")
            if local_runner is not None:
                local_runner.stop()
            return 1
        if not stream_output:
            print(answer)
        if speak_opts.enabled:
            print("sol> speaking response...", flush=True)
            ok, err = speak_with_11speak(answer, speak_opts)
            if not ok:
                print(f"warning> 11speak failed: {err}")
        if local_runner is not None:
            local_runner.stop()
        return 0

    try:
        return asyncio.run(
            run_repl(
                sol,
                search,
                include_mode=include_mode,
                include_myth=include_myth,
                include_telemetry=include_telemetry,
                include_history=include_history_repl,
                include_semantic=include_semantic,
                stream_output=stream_output,
                speak_opts=speak_opts,
                semantic_ingest_dirs=semantic_ingest_dirs,
                semantic_ingest_chunk_size=args.semantic_ingest_chunk_size,
                semantic_ingest_overlap=args.semantic_ingest_overlap,
                semantic_ingest_batch_size=args.semantic_ingest_batch_size,
                ignore_session_log_dir=bool(args.ignore_session_log_dir),
            )
        )
    finally:
        if local_runner is not None:
            local_runner.stop()


if __name__ == "__main__":
    raise SystemExit(main())
