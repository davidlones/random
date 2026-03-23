#!/usr/bin/env python3
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path(os.environ.get("RADIO_STACK_CONFIG", str(Path.home() / ".config" / "radio" / "config.yaml")))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def default_config() -> dict[str, Any]:
    state_dir = Path.home() / ".local" / "state" / "radio"
    return {
        "paths": {
            "state_dir": str(state_dir),
            "gqrx_config": str(Path.home() / ".config" / "gqrx" / "hackrf-fm.conf"),
            "xvfb_display": os.environ.get("RADIO_XVFB_DISPLAY", ":88"),
            "remote_host": "127.0.0.1",
            "remote_port": 7356,
        },
        "radio": {
            "default_frequency_mhz": 103.7,
            "default_band": "fm",
        },
        "monitor": {
            "decoders": ["EAS", "AFSK1200", "AFSK2400", "FSK9600", "POCSAG512", "POCSAG1200", "POCSAG2400", "DTMF"],
            "decoder_log": str(state_dir / "decoder.log"),
            "decoder_hits_log": str(state_dir / "decoder_hits.log"),
            "event_log": str(state_dir / "events.jsonl"),
            "transcript_log": str(state_dir / "monitor_transcript.jsonl"),
            "cortex_status": str(state_dir / "cortex" / "status.json"),
            "manual_inference_trigger": str(state_dir / "cortex" / "manual_inference.trigger"),
            "transcribe": {
                "enabled": False,
                "backend": os.environ.get("RADIO_TRANSCRIBE_BACKEND", "auto"),
                "model": os.environ.get("RADIO_TRANSCRIBE_MODEL", os.environ.get("RADIO_TRANSCRIBE_OPENAI_MODEL", "gpt-4o-transcribe")),
                "chunk_seconds": 8.0,
                "all_local": False,
            },
        },
        "transcribe": {
            "backend": os.environ.get("RADIO_TRANSCRIBE_BACKEND", "auto"),
            "all_local": False,
            "lang": "en-us",
            "model": None,
            "model_name": None,
            "openai_model": os.environ.get("RADIO_TRANSCRIBE_OPENAI_MODEL", "gpt-4o-transcribe"),
            "nemo_model": os.environ.get("RADIO_TRANSCRIBE_NEMO_MODEL", "nvidia/parakeet-tdt-0.6b-v2"),
            "nemo_device": os.environ.get("RADIO_TRANSCRIBE_NEMO_DEVICE", "auto"),
            "nemo_runtime_python": os.environ.get(
                "RADIO_TRANSCRIBE_NEMO_RUNTIME_PYTHON", str(Path.home() / ".venvs" / "radio-asr" / "bin" / "python")
            ),
            "prompt": "",
            "chunk_seconds": 8.0,
            "min_chunk_seconds": 2.0,
            "min_rms": 250,
            "partials": False,
            "min_partial_chars": 12,
            "sample_rate": 16000,
        },
        "archive": {
            "cache_hours": 2.0,
            "segment_seconds": 60,
            "permanent": False,
            "transcribe": False,
            "transcribe_backend": os.environ.get("RADIO_TRANSCRIBE_BACKEND", "auto"),
            "transcribe_model": os.environ.get("RADIO_TRANSCRIBE_MODEL", os.environ.get("RADIO_TRANSCRIBE_OPENAI_MODEL", "gpt-4o-transcribe")),
            "transcribe_prompt": "",
            "sample_rate": 16000,
            "metadata_poll_seconds": 5.0,
            "lang": "en-us",
        },
        "multichannel": {
            "rf_rate": 2_000_000,
            "sample_rate": 50_000,
            "segment_seconds": 60,
            "cache_hours": 2.0,
            "permanent": False,
            "audio_device": "",
            "transcribe": False,
            "transcribe_backend": os.environ.get("RADIO_TRANSCRIBE_BACKEND", "auto"),
            "transcribe_model": os.environ.get("RADIO_TRANSCRIBE_MODEL", os.environ.get("RADIO_TRANSCRIBE_OPENAI_MODEL", "gpt-4o-transcribe")),
            "transcribe_prompt": "",
            "lang": "en-us",
        },
        "session": {
            "degraded_timeout_seconds": int(os.environ.get("RADIO_SESSION_DEGRADED_TIMEOUT_SECONDS", "30")),
        },
        "cortex_worker": {
            "transcript_globs": [
                str(state_dir / "archive" / "jobs" / "*" / "transcripts.jsonl"),
                str(state_dir / "multichannel" / "jobs" / "*" / "channels" / "*" / "transcripts.jsonl"),
                str(state_dir / "monitor_transcript.jsonl"),
            ],
            "decoder_hits_globs": [str(state_dir / "decoder_hits.log")],
            "event_output": str(state_dir / "events.jsonl"),
            "sol_log": str(state_dir / "sol_log.txt"),
            "state_dir": str(state_dir / "cortex"),
            "working_memory_path": str(state_dir / "working_memory.json"),
            "manual_inference_trigger_path": str(state_dir / "cortex" / "manual_inference.trigger"),
            "model": {
                "path": str(Path.home() / ".cache" / "models" / "Llama-3.2-1B-Instruct-Q4_K_M.gguf"),
                "openai_model": os.environ.get("RADIO_CORTEX_OPENAI_MODEL", "gpt-5-mini"),
                "context": 2048,
                "threads": 6,
                "max_tokens": 160,
                "temperature": 0.1,
                "backend": "openai",
            },
            "processing": {
                "batch_seconds": 10,
                "idle_flush_seconds": 3,
                "inference_window_seconds": 180,
                "inference_max_items": 256,
                "poll_interval_seconds": 1.0,
                "max_batch_items": 8,
                "start_at_end": True,
                "write_narration": True,
                "min_text_chars": 12,
                "coalesce_window_seconds": 60,
                "novelty_filter": True,
                "novelty_window_seconds": 180,
                "working_memory_recent_summaries": 20,
                "dtmf_sequence_max_tones": 5,
                "dtmf_sequence_max_gap_seconds": 15,
                "dtmf_context_window_seconds": 20,
                "music_coalesce_window_seconds": 25,
                "chatter_coalesce_window_seconds": 20,
                "event_coalesce_window_seconds": 30,
            },
        },
    }


def load_radio_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    config = copy.deepcopy(default_config())
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise SystemExit(f"radio config must be a mapping: {config_path}")
        config = _deep_merge(config, raw)
    return config


def expand_path(value: str | Path) -> str:
    return os.path.expanduser(str(value))
