from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.time import now_iso, parse_timestamp


def _default_state() -> dict[str, Any]:
    return {
        "recent_summaries": [],
        "known_entities": {},
        "dtmf_patterns": {},
        "dtmf_history": [],
        "patterns": {
            "dominant_type": None,
            "cycle_length": None,
            "last_cycle_start": None,
            "last_cycle_phase": None,
            "last_change_reason": None,
        },
        "stats": {
            "total_events": 0,
            "emitted_events": 0,
            "suppressed_events": 0,
            "anomalies": 0,
        },
        "last_updated": None,
    }


class WorkingMemory:
    def __init__(self, path: Path, *, max_recent_summaries: int = 20) -> None:
        self.path = path
        self.max_recent_summaries = max_recent_summaries
        self.state = self.load()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _default_state()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _default_state()
        state = _default_state()
        state.update(payload if isinstance(payload, dict) else {})
        state["patterns"] = {**_default_state()["patterns"], **dict(state.get("patterns") or {})}
        state["stats"] = {**_default_state()["stats"], **dict(state.get("stats") or {})}
        state["recent_summaries"] = list(state.get("recent_summaries") or [])[-self.max_recent_summaries :]
        state["known_entities"] = dict(state.get("known_entities") or {})
        state["dtmf_patterns"] = dict(state.get("dtmf_patterns") or {})
        state["dtmf_history"] = list(state.get("dtmf_history") or [])[-100:]
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _normalize_text(value: Any) -> str:
        text = " ".join(str(value or "").lower().split())
        return "".join(char if char.isalnum() or char.isspace() else " " for char in text).strip()

    def detect_change(self, event: dict[str, Any]) -> dict[str, Any]:
        summary = self._normalize_text(event.get("summary"))
        event_type = str(event.get("type") or "unknown")
        patterns = self.state.get("patterns", {})
        dominant = str(patterns.get("dominant_type") or "").strip() or None
        recent = [self._normalize_text(item) for item in self.state.get("recent_summaries", []) if str(item).strip()]
        if summary and summary in recent:
            return {"is_new": False, "reason": "repetition"}
        if dominant and event_type != dominant:
            return {"is_new": True, "reason": "type_shift"}
        return {"is_new": True, "reason": "new_information"}

    def observe_event(self, event: dict[str, Any], *, suppressed: bool) -> dict[str, Any]:
        stats = self.state.setdefault("stats", {})
        patterns = self.state.setdefault("patterns", {})
        stats["total_events"] = int(stats.get("total_events", 0)) + 1
        if suppressed:
            stats["suppressed_events"] = int(stats.get("suppressed_events", 0)) + 1
        else:
            stats["emitted_events"] = int(stats.get("emitted_events", 0)) + 1
        if event.get("anomaly"):
            stats["anomalies"] = int(stats.get("anomalies", 0)) + 1

        if event.get("type") == "dtmf":
            self._observe_dtmf(event)
            self.state["last_updated"] = now_iso()
            self.save()
            return self.snapshot()

        summary = str(event.get("summary") or "").strip()
        if summary:
            recent = list(self.state.get("recent_summaries", []))
            recent.append(summary)
            self.state["recent_summaries"] = recent[-self.max_recent_summaries :]

        for key in ("entity", "location", "artist", "venue"):
            value = str(event.get(key) or "").strip()
            if value:
                known = self.state.setdefault("known_entities", {})
                known[value] = int(known.get(value, 0)) + 1

        event_type = str(event.get("type") or "unknown")
        if event_type and event_type not in {"unknown", "dtmf"}:
            patterns["dominant_type"] = event_type
        phase = str(event.get("phase") or "").strip() or None
        if phase:
            patterns["last_cycle_phase"] = phase

        event_start = parse_timestamp(event.get("window_start") or event.get("ts"))
        last_cycle_start = parse_timestamp(patterns.get("last_cycle_start"))
        if event_start is not None and last_cycle_start is not None:
            gap = event_start - last_cycle_start
            if 60 <= gap <= 180:
                patterns["cycle_length"] = round(gap, 1)
        if event.get("window_start") or event.get("ts"):
            patterns["last_cycle_start"] = event.get("window_start") or event.get("ts")

        novelty = dict(event.get("novelty") or {})
        expectation = dict(event.get("expectation") or {})
        patterns["last_change_reason"] = expectation.get("reason") or ("repeat" if not novelty.get("is_new_information", True) else "new_information")

        self.state["last_updated"] = now_iso()
        self.save()
        return self.snapshot()

    def _observe_dtmf(self, event: dict[str, Any]) -> None:
        history = list(self.state.get("dtmf_history", []))
        history.append(
            {
                "tone": str(event.get("tone") or "").strip(),
                "timestamp": event.get("timestamp") or event.get("window_start") or event.get("ts"),
                "sequence": event.get("sequence"),
                "classification": event.get("classification"),
                "channel": (event.get("channels") or ["unknown"])[0],
            }
        )
        self.state["dtmf_history"] = history[-100:]
        sequence = str(event.get("sequence") or "").strip()
        if sequence:
            patterns = self.state.setdefault("dtmf_patterns", {})
            patterns[sequence] = int(patterns.get(sequence, 0)) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "recent_summaries": list(self.state.get("recent_summaries", []))[-5:],
            "known_entities": dict(self.state.get("known_entities", {})),
            "dtmf_patterns": dict(self.state.get("dtmf_patterns", {})),
            "dtmf_history": list(self.state.get("dtmf_history", []))[-10:],
            "patterns": dict(self.state.get("patterns", {})),
            "stats": dict(self.state.get("stats", {})),
            "last_updated": self.state.get("last_updated"),
        }
