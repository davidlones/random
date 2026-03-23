#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BIN_ROOT = ROOT.parent / "bin"
if str(BIN_ROOT) not in sys.path:
    sys.path.insert(0, str(BIN_ROOT))

from event_engine import process_event
from dtmf_engine import DTMFSequenceTracker, classify_dtmf, parse_dtmf_line
from memory_graph import MemoryGraph
from prompt_templates import classify_prompt, inference_window_prompt, narration_prompt, song_identification_prompt
from radio_config import DEFAULT_CONFIG_PATH
from sol_narrator import fallback_narration, format_sol_log_line
from utils.filters import clean_text, extract_json_object, looks_like_noise
from utils.tail import LineTailer, TranscriptTailer
from utils.time import now_iso, parse_timestamp
from working_memory import WorkingMemory


DEFAULT_CONFIG = DEFAULT_CONFIG_PATH
DEFAULT_LLAMA_CLI = str(Path.home() / ".local" / "bin" / "llama-cli")
WEATHER_FREQUENCIES_MHZ = {
    "162.400",
    "162.425",
    "162.450",
    "162.475",
    "162.500",
    "162.525",
    "162.550",
}

CLASSIFY_JSON_SCHEMA: dict[str, Any] = {
    "name": "radio_activity_classification",
    "strict": False,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "type": {
                "type": "string",
                "enum": ["weather", "advisory", "emergency", "station_id", "event", "music", "chatter", "interference", "unknown"],
            },
            "content_type": {
                "type": "string",
                "enum": [
                    "weather_report",
                    "weather_advisory",
                    "station_identification",
                    "song",
                    "discussion_topic",
                    "concert",
                    "promotion",
                    "commercial",
                    "interference",
                    "unknown",
                ],
            },
            "confidence": {"type": "number"},
            "summary": {"type": "string"},
            "detailed_summary": {"type": "string"},
            "anomaly": {"type": "boolean"},
            "shared_event": {"type": "boolean"},
            "channels": {"type": "array", "items": {"type": "string"}},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "title": {"type": "string"},
            "artist": {"type": "string"},
            "topic": {"type": "string"},
            "entity": {"type": "string"},
            "location": {"type": "string"},
            "date": {"type": "string"},
            "event_type": {"type": "string"},
            "inferred": {"type": "boolean"},
        },
        "required": [
            "type",
            "content_type",
            "confidence",
            "summary",
            "detailed_summary",
            "anomaly",
            "shared_event",
            "channels",
            "reasons",
            "title",
            "artist",
            "topic",
            "entity",
            "location",
            "date",
            "event_type",
            "inferred",
        ],
    },
}

SONG_IDENTIFICATION_JSON_SCHEMA: dict[str, Any] = {
    "name": "radio_song_identification",
    "strict": False,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "artist": {"type": "string"},
            "summary": {"type": "string"},
            "confidence": {"type": "number"},
            "inferred": {"type": "boolean"},
        },
        "required": ["title", "artist", "summary", "confidence", "inferred"],
    },
}


def load_config(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "cortex_worker" in raw and isinstance(raw["cortex_worker"], dict):
        raw = raw["cortex_worker"]
    raw["transcript_globs"] = [os.path.expanduser(item) for item in raw.get("transcript_globs", [])]
    raw["decoder_hits_globs"] = [
        os.path.expanduser(item)
        for item in raw.get("decoder_hits_globs", [str(Path.home() / ".local" / "state" / "radio" / "decoder_hits.log")])
    ]
    raw["event_output"] = os.path.expanduser(raw.get("event_output", str(Path.home() / ".local" / "state" / "radio" / "events.jsonl")))
    raw["sol_log"] = os.path.expanduser(raw.get("sol_log", str(Path.home() / ".local" / "state" / "radio" / "sol_log.txt")))
    raw["state_dir"] = os.path.expanduser(raw.get("state_dir", str(Path.home() / ".local" / "state" / "radio" / "cortex")))
    raw["working_memory_path"] = os.path.expanduser(
        raw.get("working_memory_path", str(Path.home() / ".local" / "state" / "radio" / "working_memory.json"))
    )
    raw["manual_inference_trigger_path"] = os.path.expanduser(
        raw.get("manual_inference_trigger_path", str(Path.home() / ".local" / "state" / "radio" / "cortex" / "manual_inference.trigger"))
    )
    model = raw.setdefault("model", {})
    model["path"] = os.path.expanduser(model.get("path", str(Path.home() / ".cache" / "models" / "Llama-3.2-1B-Instruct-Q4_K_M.gguf")))
    model.setdefault("openai_model", os.environ.get("RADIO_CORTEX_OPENAI_MODEL", "gpt-5-mini"))
    if os.environ.get("RADIO_CORTEX_ALL_LOCAL") == "1":
        model["backend"] = "auto"
    processing = raw.setdefault("processing", {})
    processing.setdefault("batch_seconds", 10)
    processing.setdefault("idle_flush_seconds", 3)
    processing.setdefault("inference_window_seconds", 180)
    processing.setdefault("inference_max_items", 256)
    processing.setdefault("poll_interval_seconds", 1.0)
    processing.setdefault("max_batch_items", 8)
    processing.setdefault("start_at_end", True)
    processing.setdefault("write_narration", True)
    processing.setdefault("min_text_chars", 12)
    processing.setdefault("coalesce_window_seconds", 60)
    processing.setdefault("music_coalesce_window_seconds", 25)
    processing.setdefault("chatter_coalesce_window_seconds", 20)
    processing.setdefault("event_coalesce_window_seconds", 30)
    processing.setdefault("novelty_filter", True)
    processing.setdefault("novelty_window_seconds", 180)
    processing.setdefault("dtmf_sequence_max_tones", 5)
    processing.setdefault("dtmf_sequence_max_gap_seconds", 15)
    processing.setdefault("dtmf_context_window_seconds", 20)
    return raw


def write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


class LocalLlama:
    def __init__(self, config: dict[str, Any]) -> None:
        self.model_path = config["model"]["path"]
        self.openai_model = str(config["model"].get("openai_model", "gpt-5-mini"))
        self.context = int(config["model"].get("context", 2048))
        self.threads = int(config["model"].get("threads", 6))
        self.max_tokens = int(config["model"].get("max_tokens", 160))
        self.temperature = float(config["model"].get("temperature", 0.1))
        self.backend = str(config["model"].get("backend", "auto")).lower()
        self.llama_cli = os.environ.get("RADIO_CORTEX_LLAMA_CLI", DEFAULT_LLAMA_CLI)
        self._llama_cpp = None
        self._llama_cpp_unavailable = False
        self._openai_client: OpenAI | None = None
        self._openai_unavailable = False

    def _ensure_model(self) -> None:
        if not Path(self.model_path).exists():
            raise SystemExit(f"radio-cortex model not found: {self.model_path}")

    def _use_openai(self) -> bool:
        if self.backend != "openai":
            return False
        if self._openai_client is not None:
            return True
        if self._openai_unavailable:
            return False
        if not os.getenv("OPENAI_API_KEY"):
            self._openai_unavailable = True
            return False
        try:
            self._openai_client = OpenAI()
        except Exception:
            self._openai_unavailable = True
            return False
        return True

    def _use_llama_cpp(self) -> bool:
        if self.backend not in {"auto", "llama_cpp"}:
            return False
        if self._llama_cpp is not None:
            return True
        if self._llama_cpp_unavailable:
            return False
        try:
            from llama_cpp import Llama  # type: ignore
        except ModuleNotFoundError:
            self._llama_cpp_unavailable = True
            return False
        self._ensure_model()
        self._llama_cpp = Llama(
            model_path=self.model_path,
            n_ctx=self.context,
            n_threads=self.threads,
            verbose=False,
        )
        return True

    def infer_json(self, prompt: str, *, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._use_openai():
            return self._infer_openai_json(prompt, schema=schema)
        if self._use_llama_cpp():
            response = self._llama_cpp.create_completion(
                prompt=prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stop=["\n\n"],
            )
            text = response["choices"][0]["text"]
            return extract_json_object(text)
        return self._infer_via_cli(prompt)

    def infer_text(self, prompt: str) -> str:
        if self._use_openai():
            return self._infer_openai_text(prompt, max_tokens=64)
        if self._use_llama_cpp():
            response = self._llama_cpp.create_completion(
                prompt=prompt,
                temperature=self.temperature,
                max_tokens=64,
                stop=["\n\n"],
            )
            return str(response["choices"][0]["text"]).strip()
        return self._infer_cli_text(prompt, max_tokens=64)

    def _infer_via_cli(self, prompt: str) -> dict[str, Any]:
        text = self._infer_cli_text(prompt, max_tokens=self.max_tokens)
        return extract_json_object(text)

    def _infer_openai_text(self, prompt: str, *, max_tokens: int) -> str:
        if not self._use_openai() or self._openai_client is None:
            raise RuntimeError("OpenAI backend selected but OPENAI_API_KEY is not set.")
        try:
            if hasattr(self._openai_client, "responses"):
                resp = self._openai_client.responses.create(
                    model=self.openai_model,
                    input=[
                        {"role": "system", "content": [{"type": "input_text", "text": "Return plain text only. No markdown fences."}]},
                        {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                    ],
                    reasoning={"effort": "low"},
                    max_output_tokens=max(max_tokens, 200),
                )
                text = (resp.output_text or "").strip()
                if text:
                    return text
                for item in getattr(resp, "output", []) or []:
                    if getattr(item, "type", None) != "message":
                        continue
                    for content in getattr(item, "content", []) or []:
                        if getattr(content, "type", None) == "output_text":
                            text = str(getattr(content, "text", "") or "").strip()
                            if text:
                                return text
            resp = self._openai_client.chat.completions.create(
                model=self.openai_model,
                messages=[
                    {"role": "system", "content": "Return plain text only. No markdown fences."},
                    {"role": "user", "content": prompt},
                ],
                max_completion_tokens=max(max_tokens, 200),
            )
            content = resp.choices[0].message.content if resp.choices else ""
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = [item.get("text", "") for item in content if isinstance(item, dict)]
                return "\n".join(part for part in parts if part).strip()
        except Exception as exc:
            raise RuntimeError(f"OpenAI inference failed: {exc}") from exc
        return ""

    def _infer_openai_json(self, prompt: str, *, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._use_openai() or self._openai_client is None:
            raise RuntimeError("OpenAI backend selected but OPENAI_API_KEY is not set.")
        response_schema = schema or CLASSIFY_JSON_SCHEMA
        response_error: Exception | None = None
        try:
            if hasattr(self._openai_client, "responses"):
                try:
                    resp = self._openai_client.responses.create(
                        model=self.openai_model,
                        input=[
                            {"role": "system", "content": [{"type": "input_text", "text": "Return one JSON object only."}]},
                            {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                        ],
                        reasoning={"effort": "low"},
                        max_output_tokens=max(self.max_tokens * 4, 768),
                        text={
                            "verbosity": "low",
                            "format": {
                                "type": "json_schema",
                                **response_schema,
                            },
                        },
                    )
                    text = (resp.output_text or "").strip()
                    if text:
                        return extract_json_object(text)
                    for item in getattr(resp, "output", []) or []:
                        if getattr(item, "type", None) != "message":
                            continue
                        for content in getattr(item, "content", []) or []:
                            if getattr(content, "type", None) == "output_text":
                                text = str(getattr(content, "text", "") or "").strip()
                                if text:
                                    return extract_json_object(text)
                except Exception as exc:
                    response_error = exc
            resp = self._openai_client.chat.completions.create(
                model=self.openai_model,
                messages=[
                    {"role": "system", "content": "Return one JSON object only."},
                    {"role": "user", "content": prompt},
                ],
                reasoning_effort="low",
                max_completion_tokens=max(self.max_tokens * 4, 768),
                response_format={
                    "type": "json_schema",
                    "json_schema": response_schema,
                },
            )
            content = resp.choices[0].message.content if resp.choices else ""
            if isinstance(content, str) and content.strip():
                return extract_json_object(content)
            if isinstance(content, list):
                parts = [item.get("text", "") for item in content if isinstance(item, dict)]
                text = "\n".join(part for part in parts if part).strip()
                if text:
                    return extract_json_object(text)
        except Exception as exc:
            if response_error is not None:
                raise RuntimeError(f"OpenAI inference failed: responses={response_error}; chat={exc}") from exc
            raise RuntimeError(f"OpenAI inference failed: {exc}") from exc
        if response_error is not None:
            raise RuntimeError(f"OpenAI inference failed: {response_error}")
        raise RuntimeError("OpenAI inference failed: empty JSON response")

    def _infer_cli_text(self, prompt: str, *, max_tokens: int) -> str:
        self._ensure_model()
        cmd = [
            self.llama_cli,
            "-m",
            self.model_path,
            "-c",
            str(self.context),
            "-t",
            str(self.threads),
            "-n",
            str(max_tokens),
            "--temp",
            str(self.temperature),
            "--simple-io",
            "-cnv",
            "-st",
            "--no-display-prompt",
            "-p",
            prompt,
        ]
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return proc.stdout.strip()


class CortexWorker:
    def __init__(self, config: dict[str, Any], *, state_dir: Path) -> None:
        self.config = config
        self.state_dir = state_dir
        self.events_path = Path(config["event_output"])
        self.sol_log_path = Path(config["sol_log"])
        self.status_path = state_dir / "status.json"
        self.pid_path = state_dir / "worker.pid"
        self.log_path = state_dir / "worker.log"
        self.manual_inference_trigger_path = Path(config["manual_inference_trigger_path"])
        radio_state_dir = self.state_dir.parent
        self.frequency_path = radio_state_dir / "frequency.txt"
        self.session_path = radio_state_dir / "session.json"
        self.processing = config["processing"]
        self.memory = MemoryGraph()
        self.working_memory = WorkingMemory(
            Path(config["working_memory_path"]),
            max_recent_summaries=int(self.processing.get("working_memory_recent_summaries", 20)),
        )
        self.model = LocalLlama(config)
        self.stop_requested = False
        self.tailer = TranscriptTailer(
            config["transcript_globs"],
            start_at_end=bool(self.processing.get("start_at_end", True)),
        )
        self.decoder_tailer = LineTailer(
            config["decoder_hits_globs"],
            start_at_end=bool(self.processing.get("start_at_end", True)),
        )
        self.dtmf_sequences = DTMFSequenceTracker(
            max_tones=int(self.processing.get("dtmf_sequence_max_tones", 5)),
            max_gap_seconds=float(self.processing.get("dtmf_sequence_max_gap_seconds", 15)),
        )
        self.pending_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.pending_since_by_source: dict[str, float] = {}
        self.inference_pending_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.inference_pending_since_by_source: dict[str, float] = {}
        self.processed_records = 0
        self.emitted_events = 0
        self.suppressed_events = 0
        self.dtmf_events = 0
        self.open_event: dict[str, Any] | None = None
        self.last_inference_event: dict[str, Any] | None = None
        self.last_monitor_inference_event: dict[str, Any] | None = None
        self.last_manual_inference_request: str | None = None

    def current_frequency_mhz(self) -> str | None:
        try:
            value = self.frequency_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not value:
            return None
        try:
            return f"{float(value):.3f}"
        except ValueError:
            return value

    def current_radio_mode(self) -> str | None:
        freq = self.current_frequency_mhz()
        if freq in WEATHER_FREQUENCIES_MHZ:
            return "weather"
        try:
            session = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        primary = session.get("primary") or {}
        details = primary.get("details") or {}
        band = str(details.get("band") or "").strip().lower()
        if band == "weather":
            return "weather"
        return None

    def infer_record_radio_mode(self, record: dict[str, Any]) -> str | None:
        path = str(record.get("_path") or "")
        path_lower = path.lower()
        if "/archive/jobs/wx/" in path_lower:
            return "weather"
        if "/channels/wx/" in path_lower or "/channels/weather/" in path_lower:
            return "weather"
        if path_lower.endswith("/monitor_transcript.jsonl"):
            return self.current_radio_mode()
        return None

    def batch_radio_mode(self, batch: list[dict[str, Any]]) -> str | None:
        modes = [str(item.get("radio_mode") or "").strip().lower() for item in batch]
        modes = [mode for mode in modes if mode]
        if modes and all(mode == "weather" for mode in modes):
            return "weather"
        return None

    def record_source_key(self, record: dict[str, Any]) -> str:
        path = str(record.get("_path") or "")
        if path.endswith("/monitor_transcript.jsonl"):
            return "monitor"
        match = re.search(r"/archive/jobs/([^/]+)/transcripts\.jsonl$", path)
        if match:
            return f"archive:{match.group(1)}"
        match = re.search(r"/multichannel/jobs/([^/]+)/channels/([^/]+)/transcripts\.jsonl$", path)
        if match:
            return f"multichannel:{match.group(1)}:{match.group(2)}"
        return path or "unknown"

    def event_from_monitor(self, event: dict[str, Any]) -> bool:
        return any(str(path).endswith("/monitor_transcript.jsonl") for path in (event.get("source_paths") or []))

    def install_signal_handlers(self) -> None:
        def stop(_signum: int, _frame: object) -> None:
            self.stop_requested = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

    def write_status(self, **extra: Any) -> None:
        current_radio_mode = self.current_radio_mode()
        current_frequency_mhz = self.current_frequency_mhz()
        payload = {
            "active": True,
            "pid": os.getpid(),
            "updated_at": now_iso(),
            "processed_records": self.processed_records,
            "emitted_events": self.emitted_events,
            "suppressed_events": self.suppressed_events,
            "dtmf_events": self.dtmf_events,
            "pending_items": sum(len(items) for items in self.pending_by_source.values()),
            "inference_pending_items": sum(len(items) for items in self.inference_pending_by_source.values()),
            "event_output": str(self.events_path),
            "sol_log": str(self.sol_log_path),
            "working_memory_path": self.config["working_memory_path"],
            "model_path": self.config["model"]["path"],
            "backend": self.current_backend_name(),
            "openai_model": self.config["model"].get("openai_model"),
            "radio_mode": current_radio_mode,
            "current_frequency_mhz": current_frequency_mhz,
            "working_memory": self.working_memory.snapshot(),
            "last_inference": self.last_inference_event,
            "last_monitor_inference": self.last_monitor_inference_event,
            "manual_inference_trigger_path": str(self.manual_inference_trigger_path),
            **extra,
        }
        atomic_write_json(self.status_path, payload)

    def current_backend_name(self) -> str:
        if self.model._openai_client is not None:
            return "openai"
        if self.model._llama_cpp is not None:
            return "llama_cpp"
        if self.model.backend in {"auto", "llama_cli"}:
            return "llama_cli"
        return self.model.backend

    def append_sol_log(self, line: str) -> None:
        self.sol_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.sol_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")

    def enqueue(self, record: dict[str, Any]) -> None:
        if record.get("event") != "final":
            return
        text = clean_text(record.get("text"))
        if looks_like_noise(text, int(self.processing.get("min_text_chars", 12))):
            return
        enriched = dict(record)
        enriched["text"] = text
        enriched["radio_mode"] = self.infer_record_radio_mode(enriched)
        source_key = self.record_source_key(enriched)
        enriched["_source_key"] = source_key
        if not enriched.get("channel"):
            path = str(enriched.get("_path") or "")
            parts = Path(path).parts
            if "channels" in parts:
                idx = parts.index("channels")
                if idx + 1 < len(parts):
                    enriched["channel"] = parts[idx + 1]
        self.pending_by_source[source_key].append(enriched)
        if source_key not in self.pending_since_by_source:
            self.pending_since_by_source[source_key] = time.time()
        self.inference_pending_by_source[source_key].append(enriched)
        max_items = max(1, int(self.processing.get("inference_max_items", 256)))
        if len(self.inference_pending_by_source[source_key]) > max_items:
            self.inference_pending_by_source[source_key] = self.inference_pending_by_source[source_key][-max_items:]
        if source_key not in self.inference_pending_since_by_source:
            self.inference_pending_since_by_source[source_key] = time.time()

    def ready_pending_source(self) -> str | None:
        now = time.time()
        ready: list[tuple[float, str]] = []
        for source_key, items in self.pending_by_source.items():
            pending_since = self.pending_since_by_source.get(source_key)
            if not items or pending_since is None:
                continue
            if len(items) >= int(self.processing.get("max_batch_items", 8)):
                ready.append((pending_since, source_key))
                continue
            if now - pending_since >= float(self.processing.get("batch_seconds", 10)):
                ready.append((pending_since, source_key))
        if not ready:
            return None
        ready.sort()
        return ready[0][1]

    def idle_pending_source(self) -> str | None:
        now = time.time()
        ready: list[tuple[float, str]] = []
        for source_key, items in self.pending_by_source.items():
            pending_since = self.pending_since_by_source.get(source_key)
            if not items or pending_since is None:
                continue
            if now - pending_since >= float(self.processing.get("idle_flush_seconds", 3)):
                ready.append((pending_since, source_key))
        if not ready:
            return None
        ready.sort()
        return ready[0][1]

    def flush(self, source_key: str) -> None:
        batch = self.pending_by_source.pop(source_key, [])
        self.pending_since_by_source.pop(source_key, None)
        if not batch:
            return
        prompt = classify_prompt(batch, allow_inference_details=False, assumed_mode=self.batch_radio_mode(batch))
        try:
            parsed = self.model.infer_json(prompt)
        except Exception as exc:
            self.write_status(last_error=f"model inference failed: {exc}")
            return
        self.handle_candidate(process_event(parsed, batch))

    def ready_inference_source(self) -> str | None:
        now = time.time()
        ready: list[tuple[float, str]] = []
        for source_key, items in self.inference_pending_by_source.items():
            pending_since = self.inference_pending_since_by_source.get(source_key)
            if not items or pending_since is None:
                continue
            if now - pending_since >= float(self.processing.get("inference_window_seconds", 180)):
                ready.append((pending_since, source_key))
        if not ready:
            return None
        ready.sort()
        return ready[0][1]

    def manual_inference_source(self) -> str | None:
        if self.inference_pending_by_source.get("monitor"):
            return "monitor"
        ready: list[tuple[float, str]] = []
        for source_key, items in self.inference_pending_by_source.items():
            pending_since = self.inference_pending_since_by_source.get(source_key)
            if not items or pending_since is None:
                continue
            ready.append((pending_since, source_key))
        if not ready:
            return None
        ready.sort()
        return ready[0][1]

    def consume_manual_inference_request(self) -> bool:
        try:
            token = self.manual_inference_trigger_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return False
        except OSError as exc:
            self.write_status(last_error=f"manual inference trigger read failed: {exc}")
            return False
        if not token or token == self.last_manual_inference_request:
            return False
        self.last_manual_inference_request = token
        return True

    def build_fallback_inference_event(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        batch_mode = self.batch_radio_mode(batch)
        fallback = process_event(
            {
                "type": "unknown",
                "content_type": "unknown",
                "confidence": 0.2,
                "summary": "",
                "detailed_summary": "",
                "anomaly": False,
                "shared_event": False,
                "channels": [],
                "reasons": ["heuristic_inference_fallback"],
                "title": "",
                "artist": "",
                "topic": "",
                "entity": "",
                "location": "",
                "date": "",
                "event_type": "",
                "inferred": True,
            },
            batch,
        )
        snippet = ""
        for item in batch:
            text = clean_text(item.get("text"))
            if text:
                snippet = text.rstrip(" .")
                break
        if len(snippet) > 96:
            snippet = snippet[:93].rstrip() + "..."
        if batch_mode == "weather" and snippet and fallback.get("type") in {"weather", "advisory", "station_id"}:
            fallback["summary"] = str(fallback.get("summary") or snippet)
        elif fallback.get("type") == "music" and not fallback.get("title") and snippet:
            fallback["summary"] = f"song fragment: {snippet}"
        elif fallback.get("type") == "event" and snippet and not str(fallback.get("summary") or "").strip():
            fallback["summary"] = f"promo or ad: {snippet}"
        elif fallback.get("type") == "chatter" and snippet and not str(fallback.get("topic") or "").strip():
            fallback["topic"] = snippet
            fallback["summary"] = f"discussion topic: {snippet}"
        return fallback

    def likely_music_window(self, batch: list[dict[str, Any]]) -> bool:
        if self.batch_radio_mode(batch) == "weather":
            return False
        texts = [clean_text(item.get("text")) for item in batch if clean_text(item.get("text"))]
        if len(texts) < 3:
            return False
        joined = " ".join(texts).lower()
        promo_markers = ("tickets", "concert", "sponsored", "sale", "visit ", "call now", "buy now")
        if any(marker in joined for marker in promo_markers):
            return False
        short_lines = 0
        repeated_starts = 0
        seen_starts: set[str] = set()
        for text in texts[:10]:
            words = text.split()
            if 2 <= len(words) <= 8:
                short_lines += 1
            start = " ".join(words[:2]).lower() if len(words) >= 2 else text.lower()
            if start in seen_starts:
                repeated_starts += 1
            else:
                seen_starts.add(start)
        return short_lines >= min(len(texts), 3) or repeated_starts >= 1

    def coerce_music_inference_event(self, batch: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any]:
        if not self.likely_music_window(batch):
            return event
        if event.get("type") == "event":
            return event
        event["type"] = "music"
        event["content_type"] = "song"
        event["topic"] = None
        if not str(event.get("summary") or "").strip() or event.get("summary", "").lower().startswith("discussion topic:"):
            snippet = ""
            for item in batch:
                text = clean_text(item.get("text"))
                if text:
                    snippet = text.rstrip(" .")
                    break
            if snippet:
                event["summary"] = f"song fragment: {snippet}"
        event["reasons"] = list(dict.fromkeys([*event.get("reasons", []), "lyric_window_override"]))
        return event

    def line_looks_like_promo(self, text: str) -> bool:
        lowered = text.lower()
        promo_markers = (
            "alt 103",
            "music discovery",
            "commercial-free",
            "tickets",
            "presented by",
            "visit ",
            "call ",
            "sponsored",
            "sale",
            ".com",
            "outback presents",
            "wells fargo",
            "autonation",
            "pre-k",
            "registration",
        )
        return any(marker in lowered for marker in promo_markers)

    def song_identification_batch(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for item in batch:
            text = clean_text(item.get("text"))
            if not text:
                continue
            if self.line_looks_like_promo(text):
                continue
            filtered.append(item)
        trimmed = filtered or batch
        return trimmed[-5:]

    def song_candidate_context(self) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        for event in reversed(self.memory.recent_events(24)):
            if event.get("content_type") != "song":
                continue
            title = clean_text(event.get("title"))
            artist = clean_text(event.get("artist"))
            summary = clean_text(event.get("summary"))
            if title:
                label = f"{title} by {artist}" if artist else title
                if label not in seen:
                    candidates.append(label)
                    seen.add(label)
            elif summary and "song" in summary.lower() and summary not in seen:
                candidates.append(summary)
                seen.add(summary)
            if len(candidates) >= 5:
                return candidates
        known_entities = dict(self.working_memory.snapshot().get("known_entities") or {})
        skip_terms = {
            "dallas",
            "texas",
            "dfw",
            "airport",
            "amazon hub delivery",
            "wells fargo",
            "autonation",
            "prekdallas.org",
            "radio station (unnamed)",
        }
        musicish: list[tuple[int, str]] = []
        for name, count in known_entities.items():
            label = clean_text(name)
            lowered = label.lower()
            if not label or lowered in skip_terms:
                continue
            if any(token in lowered for token in (".com", "http", ";", "school", "bank", "theater", "arena", "presents")):
                continue
            if any(ch.isdigit() for ch in label):
                continue
            words = label.split()
            if len(words) > 4:
                continue
            musicish.append((int(count), label))
        musicish.sort(key=lambda item: (-item[0], item[1]))
        for _count, label in musicish:
            if label in seen:
                continue
            candidates.append(label)
            seen.add(label)
            if len(candidates) >= 8:
                break
        return candidates

    def enrich_song_inference(self, batch: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any]:
        if self.batch_radio_mode(batch) == "weather":
            return event
        likely_music = self.likely_music_window(batch)
        is_song_event = event.get("type") == "music" and event.get("content_type") == "song"
        if not is_song_event and not likely_music:
            return event
        if str(event.get("title") or "").strip():
            return event
        filtered_batch = self.song_identification_batch(batch)
        prompt = song_identification_prompt(filtered_batch, candidate_context=self.song_candidate_context())
        try:
            parsed = self.model.infer_json(prompt, schema=SONG_IDENTIFICATION_JSON_SCHEMA)
        except Exception:
            return event
        title = clean_text(parsed.get("title"))
        artist = clean_text(parsed.get("artist"))
        summary = clean_text(parsed.get("summary"))
        inferred = parsed.get("inferred")
        if title:
            event["type"] = "music"
            event["content_type"] = "song"
            event["topic"] = None
            event["entity"] = None
            event["location"] = None
            event["date"] = None
            event["event_type"] = None
            event["title"] = title
            if artist:
                event["artist"] = artist
            event["inferred"] = True if inferred is None else bool(inferred)
            event["summary"] = f"inferred song: {title}"
            if event.get("artist"):
                event["summary"] = f"{event['summary']} by {event['artist']}"
            event["reasons"] = list(dict.fromkeys([*event.get("reasons", []), "song_identification"]))
        elif summary:
            if likely_music:
                event["type"] = "music"
                event["content_type"] = "song"
                event["topic"] = None
                event["summary"] = summary
                event["reasons"] = list(dict.fromkeys([*event.get("reasons", []), "song_fragment_inference"]))
            elif is_song_event:
                event["summary"] = summary
        return event

    def is_meaningful_inference_event(self, event: dict[str, Any]) -> bool:
        if event.get("type") in {"weather", "advisory", "station_id", "emergency"}:
            return bool(str(event.get("summary") or event.get("detailed_summary") or "").strip())
        if event.get("type") == "music" and event.get("content_type") == "song":
            return bool(str(event.get("summary") or "").strip())
        if event.get("type") == "chatter" and event.get("content_type") == "discussion_topic":
            return bool(str(event.get("topic") or event.get("summary") or "").strip())
        if event.get("type") == "event":
            return any(
                bool(str(event.get(key) or "").strip())
                for key in ("summary", "entity", "location", "date", "event_type")
            )
        return False

    def update_last_inference(self, event: dict[str, Any]) -> None:
        content_type = str(event.get("content_type") or "")
        title = event.get("title")
        artist = event.get("artist")
        summary = event.get("summary")
        topic = event.get("topic") if content_type == "discussion_topic" else None
        if content_type == "song" and not title and summary:
            title = summary
            artist = None
        self.last_inference_event = {
            "ts": event.get("ts"),
            "window_start": event.get("window_start"),
            "window_end": event.get("window_end"),
            "type": event.get("type"),
            "content_type": event.get("content_type"),
            "summary": summary,
            "title": title,
            "artist": artist,
            "topic": topic,
            "entity": event.get("entity"),
            "location": event.get("location"),
            "date": event.get("date"),
            "event_type": event.get("event_type"),
            "radio_mode": event.get("radio_mode"),
            "inferred": bool(event.get("inferred")),
        }
        if self.event_from_monitor(event):
            self.last_monitor_inference_event = dict(self.last_inference_event)

    def flush_inference(self, source_key: str, *, force: bool = False) -> None:
        batch = self.inference_pending_by_source.pop(source_key, [])
        self.inference_pending_since_by_source.pop(source_key, None)
        if not batch:
            return
        batch_mode = self.batch_radio_mode(batch)
        if batch_mode == "weather":
            prompt = classify_prompt(batch, allow_inference_details=False, assumed_mode=batch_mode)
        else:
            prompt = inference_window_prompt(batch, assumed_mode=batch_mode)
        try:
            parsed = self.model.infer_json(prompt)
        except Exception as exc:
            self.write_status(last_error=f"inference window failed: {exc}")
            return
        event = process_event(parsed, batch)
        if not self.is_meaningful_inference_event(event):
            event = self.build_fallback_inference_event(batch)
        if batch_mode != "weather":
            event = self.coerce_music_inference_event(batch, event)
            event = self.enrich_song_inference(batch, event)
        if not self.is_meaningful_inference_event(event):
            self.write_status(last_inference_attempted_at=now_iso())
            return
        event["reasons"] = list(dict.fromkeys([*event.get("reasons", []), "inference_window"]))
        event["inference_window_seconds"] = int(float(self.processing.get("inference_window_seconds", 180)))
        event["inference_window_items"] = len(batch)
        self.update_last_inference(event)
        self.write_status(last_inference_attempted_at=now_iso())
        self.emit_event(event)

    def handle_decoder_record(self, record: dict[str, Any]) -> None:
        event = parse_dtmf_line(str(record.get("text") or ""))
        if not event:
            return
        parsed_ts = parse_timestamp(event.get("timestamp"))
        sequence_state = self.dtmf_sequences.update(event, parsed_ts)
        event["sequence"] = sequence_state["sequence"]
        event["sequence_length"] = sequence_state["length"]
        event["classification"] = classify_dtmf(str(event.get("tone") or ""), sequence_state["sequence"])
        event["confidence"] = 0.8 if event["classification"] == "control" else 0.65
        event["channels"] = [str(event.get("channel") or "unknown")]
        event["source_paths"] = [str(record.get("_path") or "")]
        event["window_start"] = event.get("timestamp")
        event["window_end"] = event.get("timestamp")
        event["shared_event"] = False
        event["anomaly"] = event["classification"] == "control"
        event["keywords"] = [f"dtmf:{event['tone']}", f"sequence:{event['sequence']}"]
        event["reasons"] = ["decoder_hits"]
        event["phase"] = "control_signal"
        event["items"] = [{"ts": event.get("timestamp"), "channel": event.get("channel"), "text": event.get("raw_line")}]
        self.attach_dtmf_context(event)
        self.emit_dtmf_event(event)

    def attach_dtmf_context(self, event: dict[str, Any]) -> None:
        related = self.find_related_audio_event(event)
        if not related:
            event["summary"] = f"DTMF {event['tone']} detected"
            event["context_summary"] = None
            event["linked_event_type"] = None
            return
        event["context_summary"] = related.get("summary")
        event["linked_event_type"] = related.get("type")
        if event.get("sequence_length", 0) >= 2:
            event["summary"] = f"DTMF sequence {event['sequence']} near {related.get('type', 'audio')} content"
        else:
            event["summary"] = f"DTMF {event['tone']} near {related.get('type', 'audio')} content"

    def find_related_audio_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        event_ts = parse_timestamp(event.get("timestamp"))
        if event_ts is None:
            return self.open_event
        candidates: list[dict[str, Any]] = []
        if self.open_event is not None:
            candidates.append(self.open_event)
        candidates.extend(self.memory.recent_events(12))
        window = float(self.processing.get("dtmf_context_window_seconds", 20))
        best: dict[str, Any] | None = None
        best_gap: float | None = None
        for candidate in reversed(candidates):
            if candidate.get("type") == "dtmf":
                continue
            candidate_ts = parse_timestamp(candidate.get("window_end") or candidate.get("window_start") or candidate.get("ts"))
            if candidate_ts is None:
                continue
            gap = abs(event_ts - candidate_ts)
            if gap > window:
                continue
            if best is None or best_gap is None or gap < best_gap:
                best = candidate
                best_gap = gap
        return best

    def handle_candidate(self, event: dict[str, Any]) -> None:
        if self.open_event is None:
            self.open_event = event
            self.write_status(last_event_type=event["type"], last_event_summary=event["summary"])
            return
        if self.should_coalesce(self.open_event, event):
            self.open_event = self.merge_events(self.open_event, event)
            self.write_status(last_event_type=self.open_event["type"], last_event_summary=self.open_event["summary"])
            return
        self.emit_event(self.open_event)
        self.open_event = event
        self.write_status(last_event_type=event["type"], last_event_summary=event["summary"])

    def emit_event(self, event: dict[str, Any]) -> None:
        novelty = self.memory.novelty_state(
            event,
            window_seconds=float(self.processing.get("novelty_window_seconds", 180)),
        )
        event["novelty"] = novelty
        expectation = self.working_memory.detect_change(event)
        event["expectation"] = expectation
        suppressed = self.should_suppress_event(event, novelty, expectation)
        if suppressed:
            self.working_memory.observe_event(event, suppressed=True)
            self.suppressed_events += 1
            self.write_status(
                last_suppressed_type=event.get("type"),
                last_suppressed_summary=event.get("summary"),
                last_suppressed_reason=expectation.get("reason"),
                last_event_type=event.get("type"),
                last_event_summary=event.get("summary"),
            )
            return
        self.memory.add_event(event)
        memory = self.memory.snapshot()
        working_memory = self.working_memory.observe_event(event, suppressed=False)
        event["memory"] = memory
        event["working_memory"] = working_memory
        write_jsonl(self.events_path, event)
        self.emitted_events += 1
        if self.processing.get("write_narration", True):
            combined_memory = {**memory, "working_memory": working_memory}
            narration = fallback_narration(event, combined_memory)
            if self.model._llama_cpp is not None:
                try:
                    llm_narration = self.model.infer_text(narration_prompt(event, combined_memory)).strip()
                    if llm_narration:
                        narration = llm_narration
                except Exception:
                    pass
            self.append_sol_log(format_sol_log_line(event, narration))

    def emit_dtmf_event(self, event: dict[str, Any]) -> None:
        self.memory.add_event(event)
        memory = self.memory.snapshot()
        working_memory = self.working_memory.observe_event(event, suppressed=False)
        event["memory"] = memory
        event["working_memory"] = working_memory
        write_jsonl(self.events_path, event)
        self.emitted_events += 1
        self.dtmf_events += 1
        self.write_status(
            last_event_type=event.get("type"),
            last_event_summary=event.get("summary"),
            last_dtmf_tone=event.get("tone"),
            last_dtmf_sequence=event.get("sequence"),
        )
        if self.processing.get("write_narration", True):
            narration = fallback_narration(event, {**memory, "working_memory": working_memory})
            self.append_sol_log(format_sol_log_line(event, narration))

    def should_suppress_event(self, event: dict[str, Any], novelty: dict[str, Any], expectation: dict[str, Any]) -> bool:
        if not self.processing.get("novelty_filter", True):
            return False
        if event.get("anomaly") or event.get("type") == "emergency":
            return False
        if self.is_low_value_fragment(event):
            return True
        return (not bool(novelty.get("is_new_information", True))) or (not bool(expectation.get("is_new", True)))

    def should_coalesce(self, current: dict[str, Any], incoming: dict[str, Any]) -> bool:
        if current.get("type") != incoming.get("type"):
            return False
        if sorted(current.get("channels", [])) != sorted(incoming.get("channels", [])):
            return False
        current_paths = {str(path) for path in current.get("source_paths", []) if str(path).strip()}
        incoming_paths = {str(path) for path in incoming.get("source_paths", []) if str(path).strip()}
        if current_paths and incoming_paths and not current_paths.intersection(incoming_paths):
            return False
        current_end = parse_timestamp(current.get("window_end") or current.get("window_start") or current.get("ts"))
        incoming_start = parse_timestamp(incoming.get("window_start") or incoming.get("ts"))
        if current_end is None or incoming_start is None:
            return False
        gap = incoming_start - current_end
        event_type = str(current.get("type") or "")
        if event_type in {"weather", "advisory", "station_id"}:
            return gap <= float(self.processing.get("coalesce_window_seconds", 60))
        if event_type == "music" and current.get("content_type") == incoming.get("content_type") == "song":
            return gap <= float(self.processing.get("music_coalesce_window_seconds", 25))
        if event_type == "chatter" and current.get("content_type") == incoming.get("content_type") == "discussion_topic":
            return gap <= float(self.processing.get("chatter_coalesce_window_seconds", 20))
        if event_type == "event" and current.get("content_type") == incoming.get("content_type"):
            return gap <= float(self.processing.get("event_coalesce_window_seconds", 30))
        return False

    def merge_events(self, current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged_items = list(current.get("items", [])) + list(incoming.get("items", []))
        phase = incoming.get("phase") or current.get("phase")
        merged_texts = [
            clean_text(item.get("text"))
            for item in merged_items
            if clean_text(item.get("text"))
        ]
        full_text = ". ".join(text.rstrip(" .") for text in merged_texts) if merged_texts else None
        detailed_summary = current.get("detailed_summary") or incoming.get("detailed_summary")
        if current.get("type") in {"weather", "advisory", "station_id"}:
            detailed_summary = full_text or detailed_summary or self.choose_summary(current, incoming)
        merged = {
            **current,
            "ts": incoming.get("ts") or current.get("ts"),
            "window_end": incoming.get("window_end") or incoming.get("window_start") or current.get("window_end"),
            "summary": self.choose_summary(current, incoming),
            "detailed_summary": detailed_summary,
            "full_text": full_text,
            "confidence": round(max(float(current.get("confidence") or 0.0), float(incoming.get("confidence") or 0.0)), 3),
            "anomaly": bool(current.get("anomaly") or incoming.get("anomaly")),
            "shared_event": bool(current.get("shared_event") or incoming.get("shared_event")),
            "keywords": sorted({*current.get("keywords", []), *incoming.get("keywords", [])}),
            "reasons": list(dict.fromkeys([*current.get("reasons", []), *incoming.get("reasons", [])])),
            "source_paths": sorted({*current.get("source_paths", []), *incoming.get("source_paths", [])}),
            "items": merged_items[-32:],
            "phase": phase,
            "content_type": incoming.get("content_type") or current.get("content_type"),
            "title": incoming.get("title") or current.get("title"),
            "artist": incoming.get("artist") or current.get("artist"),
            "topic": incoming.get("topic") or current.get("topic"),
            "entity": incoming.get("entity") or current.get("entity"),
            "location": incoming.get("location") or current.get("location"),
            "date": incoming.get("date") or current.get("date"),
            "event_type": incoming.get("event_type") or current.get("event_type"),
            "inferred": bool(current.get("inferred") or incoming.get("inferred")),
            "coalesced": True,
            "coalesced_items": len(merged_items),
        }
        return merged

    def choose_summary(self, current: dict[str, Any], incoming: dict[str, Any]) -> str:
        candidates = [str(incoming.get("summary") or "").strip(), str(current.get("summary") or "").strip()]
        candidates = [item for item in candidates if item]
        if not candidates:
            return str(incoming.get("type") or current.get("type") or "unknown")
        candidates.sort(key=lambda item: (len(item.split()) < 3, -len(item)))
        return candidates[0]

    def is_low_value_fragment(self, event: dict[str, Any]) -> bool:
        event_type = str(event.get("type") or "")
        content_type = str(event.get("content_type") or "")
        summary = str(event.get("summary") or "").strip().lower()
        items = list(event.get("items") or [])
        if event_type == "music" and content_type == "song":
            if event.get("title") or event.get("artist"):
                return False
            generic_markers = (
                "short lyric fragment",
                "brief song lyric fragment",
                "current short lyric fragment",
                "lyric fragment",
                "song lyric",
            )
            return len(items) <= 1 and any(marker in summary for marker in generic_markers)
        if event_type == "chatter" and content_type == "discussion_topic":
            generic_markers = (
                "no clear topic or context",
                "declarative remark",
                "brief spoken fragment",
            )
            return len(items) <= 1 and any(marker in summary for marker in generic_markers)
        return False

    def run(self, *, once: bool = False) -> int:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        self.install_signal_handlers()
        self.write_status(started_at=now_iso())
        try:
            while not self.stop_requested:
                records = self.tailer.poll()
                decoder_records = self.decoder_tailer.poll()
                for record in records:
                    self.processed_records += 1
                    self.enqueue(record)
                for record in decoder_records:
                    self.processed_records += 1
                    self.handle_decoder_record(record)
                manual_inference_requested = self.consume_manual_inference_request()
                ready_batch_source = self.ready_pending_source()
                if ready_batch_source is None and once and self.pending_by_source:
                    ready_batch_source = next(iter(self.pending_by_source))
                if ready_batch_source is not None:
                    self.flush(ready_batch_source)
                if manual_inference_requested:
                    self.write_status(last_manual_inference_requested_at=now_iso())
                    manual_source = self.manual_inference_source()
                    if manual_source is not None:
                        self.flush_inference(manual_source, force=True)
                else:
                    ready_inference_source = self.ready_inference_source()
                    if ready_inference_source is None and once and self.inference_pending_by_source:
                        ready_inference_source = self.manual_inference_source()
                    if ready_inference_source is not None:
                        self.flush_inference(ready_inference_source, force=once)
                    elif not records and not decoder_records:
                        idle_source = self.idle_pending_source()
                        if idle_source is not None:
                            self.flush(idle_source)
                self.write_status()
                if once:
                    break
                time.sleep(float(self.processing.get("poll_interval_seconds", 1.0)))
            while self.pending_by_source:
                self.flush(next(iter(self.pending_by_source)))
            while self.inference_pending_by_source:
                source_key = self.manual_inference_source()
                if source_key is None:
                    break
                self.flush_inference(source_key, force=True)
            if self.open_event is not None:
                self.emit_event(self.open_event)
                self.open_event = None
            return 0
        finally:
            self.write_status(active=False, stopped_at=now_iso())
            try:
                self.pid_path.unlink()
            except FileNotFoundError:
                pass


def is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def start_daemon(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    state_dir = Path(config["state_dir"])
    pid_path = state_dir / "worker.pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = 0
        if pid and is_pid_running(pid):
            raise SystemExit(f"radio-cortex is already running (pid {pid})")
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "worker.log"
    log_handle = log_path.open("a", encoding="utf-8")
    cmd = [sys.executable, str(Path(__file__)), "run", "--config", str(args.config)]
    proc = subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(1)
    if proc.poll() is not None:
        raise SystemExit(f"radio-cortex exited immediately; check {log_path}")
    print(f"radio-cortex started (pid {proc.pid})")
    print(f"state_dir: {state_dir}")
    print(f"log: {log_path}")
    return 0


def stop_daemon(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    pid_path = Path(config["state_dir"]) / "worker.pid"
    if not pid_path.exists():
        raise SystemExit("radio-cortex is not running")
    pid = int(pid_path.read_text(encoding="utf-8").strip())
    os.kill(pid, signal.SIGTERM)
    print(f"stopping radio-cortex (pid {pid})")
    return 0


def status_daemon(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    state_dir = Path(config["state_dir"])
    status_path = state_dir / "status.json"
    pid_path = state_dir / "worker.pid"
    pid = int(pid_path.read_text(encoding="utf-8").strip()) if pid_path.exists() else None
    active = bool(pid and is_pid_running(pid))
    if not status_path.exists():
        print(f"active: {'yes' if active else 'no'}")
        print(f"state_dir: {state_dir}")
        return 0
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    print(f"active: {'yes' if active else 'no'}")
    print(f"pid: {pid if pid else 'n/a'}")
    print(f"processed_records: {status.get('processed_records', 0)}")
    print(f"emitted_events: {status.get('emitted_events', 0)}")
    print(f"dtmf_events: {status.get('dtmf_events', 0)}")
    print(f"suppressed_events: {status.get('suppressed_events', 0)}")
    print(f"pending_items: {status.get('pending_items', 0)}")
    print(f"inference_pending_items: {status.get('inference_pending_items', 0)}")
    print(f"last_event_type: {status.get('last_event_type', 'n/a')}")
    print(f"last_event_summary: {status.get('last_event_summary', 'n/a')}")
    last_inference = status.get("last_inference") or {}
    print(f"last_inference_summary: {last_inference.get('summary', 'n/a')}")
    print(f"last_inference_window_end: {last_inference.get('window_end', 'n/a')}")
    print(f"event_output: {status.get('event_output', 'n/a')}")
    print(f"sol_log: {status.get('sol_log', 'n/a')}")
    print(f"working_memory_path: {status.get('working_memory_path', 'n/a')}")
    print(f"radio_mode: {status.get('radio_mode', 'n/a')}")
    print(f"current_frequency_mhz: {status.get('current_frequency_mhz', 'n/a')}")
    print(f"backend: {status.get('backend', 'n/a')}")
    print(f"openai_model: {status.get('openai_model', 'n/a')}")
    return 0


def log_daemon(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    log_path = Path(config["state_dir"]) / "worker.log"
    if not log_path.exists():
        raise SystemExit(f"no radio-cortex log at {log_path}")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-args.lines :]:
        print(line)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local radio-cortex worker for transcript interpretation.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("start", "stop", "status", "log", "run"):
        sub_parser = sub.add_parser(name)
        sub_parser.add_argument("--config", default=str(DEFAULT_CONFIG))

    sub.choices["status"].add_argument("--json", action="store_true")
    sub.choices["log"].add_argument("--lines", type=int, default=40)
    sub.choices["run"].add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.cmd == "start":
        return start_daemon(args)
    if args.cmd == "stop":
        return stop_daemon(args)
    if args.cmd == "status":
        return status_daemon(args)
    if args.cmd == "log":
        return log_daemon(args)
    if args.cmd == "run":
        config = load_config(Path(args.config))
        state_dir = Path(config["state_dir"])
        return CortexWorker(config, state_dir=state_dir).run(once=args.once)
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
