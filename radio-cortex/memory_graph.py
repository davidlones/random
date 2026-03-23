from __future__ import annotations

from collections import Counter, deque
from typing import Any

from utils.time import parse_timestamp


class MemoryGraph:
    """Retains a bounded event history and computes small recent-memory summaries.

    `max_retained` caps in-memory history size. `snapshot(window=32)` controls how
    much of that retained history is queried for the current summary.
    """

    def __init__(self, max_retained: int = 256) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max_retained)

    def add_event(self, event: dict[str, Any]) -> None:
        self._events.append(event)

    @staticmethod
    def normalize_summary(summary: str | None) -> str:
        text = " ".join(str(summary or "").lower().split())
        return "".join(char if char.isalnum() or char.isspace() else " " for char in text).strip()

    def _recent_events(self, window: int = 32) -> list[dict[str, Any]]:
        return list(self._events)[-window:]

    def recent_events(self, window: int = 32) -> list[dict[str, Any]]:
        return self._recent_events(window)

    def novelty_state(self, candidate: dict[str, Any], *, window_seconds: float = 180.0) -> dict[str, Any]:
        current_start = parse_timestamp(candidate.get("window_start") or candidate.get("ts"))
        current_summary = self.normalize_summary(str(candidate.get("summary") or ""))
        current_type = str(candidate.get("type") or "unknown")
        current_channels = tuple(sorted(str(channel) for channel in candidate.get("channels", []) if channel))
        matches: list[dict[str, Any]] = []
        for event in reversed(self._events):
            event_start = parse_timestamp(event.get("window_start") or event.get("ts"))
            if current_start is not None and event_start is not None and (current_start - event_start) > window_seconds:
                break
            event_type = str(event.get("type") or "unknown")
            event_channels = tuple(sorted(str(channel) for channel in event.get("channels", []) if channel))
            event_summary = self.normalize_summary(str(event.get("summary") or ""))
            if event_type == current_type and event_channels == current_channels and event_summary == current_summary and event_summary:
                matches.append(event)
        latest_match = matches[0] if matches else None
        repeat_count = len(matches)
        cycle = self.cycle_context(candidate)
        return {
            "is_new_information": latest_match is None,
            "repeat_count": repeat_count,
            "last_seen_ts": latest_match.get("ts") if latest_match else None,
            "recent_summaries": [
                str(event.get("summary") or "")
                for event in self._recent_events(8)
                if str(event.get("summary") or "").strip()
            ],
            **cycle,
        }

    def cycle_context(self, candidate: dict[str, Any]) -> dict[str, Any]:
        recent = self._recent_events(32)
        if not recent:
            return {"cycle_detected": False, "cycle_length_seconds": None, "cycle_phase": candidate.get("phase"), "progress": None}
        candidate_start = parse_timestamp(candidate.get("window_start") or candidate.get("ts"))
        cycle_length_seconds = None
        cycle_match_start = None
        candidate_type = candidate.get("type")
        candidate_phase = candidate.get("phase")
        candidate_channels = sorted(candidate.get("channels", []))
        for earlier in reversed(recent):
            if earlier.get("type") != candidate_type:
                continue
            if sorted(earlier.get("channels", [])) != candidate_channels:
                continue
            earlier_start = parse_timestamp(earlier.get("window_start") or earlier.get("ts"))
            if candidate_start is None or earlier_start is None:
                continue
            gap = candidate_start - earlier_start
            if gap < 45 or gap > 180:
                continue
            if earlier.get("phase") == candidate_phase or self.normalize_summary(earlier.get("summary")) == self.normalize_summary(candidate.get("summary")):
                cycle_length_seconds = round(gap, 1)
                cycle_match_start = earlier_start
                break
        progress = None
        if candidate_start is not None and cycle_length_seconds:
            if cycle_match_start is not None:
                progress = 0.0
        return {
            "cycle_detected": bool(cycle_length_seconds),
            "cycle_length_seconds": cycle_length_seconds,
            "cycle_phase": candidate_phase,
            "progress": progress,
        }

    def snapshot(self, window: int = 32) -> dict[str, Any]:
        recent = self._recent_events(window)
        counts = Counter(event.get("type") for event in recent if event.get("type"))
        anomalies = sum(1 for event in recent if event.get("anomaly"))
        cycle_detected = False
        cycle_length_seconds = None
        phase = recent[-1].get("phase") if recent else None
        if recent:
            latest = recent[-1]
            latest_start = parse_timestamp(latest.get("window_start") or latest.get("ts"))
            latest_summary = str(latest.get("summary") or "").lower()
            for earlier in reversed(recent[:-1]):
                if earlier.get("type") != latest.get("type"):
                    continue
                earlier_start = parse_timestamp(earlier.get("window_start") or earlier.get("ts"))
                if latest_start is None or earlier_start is None:
                    continue
                gap = latest_start - earlier_start
                if gap < 45 or gap > 180:
                    continue
                earlier_summary = str(earlier.get("summary") or "").lower()
                if latest_summary and earlier_summary and (
                    latest_summary == earlier_summary
                    or latest.get("phase") == earlier.get("phase")
                ):
                    cycle_detected = True
                    cycle_length_seconds = round(gap, 1)
                    break
        return {
            "count": len(recent),
            "dominant_type": counts.most_common(1)[0][0] if counts else None,
            "anomalies": anomalies,
            "cycle_detected": cycle_detected,
            "cycle_length_seconds": cycle_length_seconds,
            "phase": phase,
            "recent_summaries": [
                str(event.get("summary") or "")
                for event in recent[-5:]
                if str(event.get("summary") or "").strip()
            ],
            "recent_channels": sorted(
                {
                    channel
                    for event in recent
                    for channel in event.get("channels", [])
                    if channel
                }
            ),
        }
