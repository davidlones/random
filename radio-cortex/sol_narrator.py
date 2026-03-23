from __future__ import annotations

from typing import Any


def fallback_narration(event: dict[str, Any], memory: dict[str, Any]) -> str:
    summary = str(event.get("summary") or event.get("type") or "unknown activity").strip()
    working = dict(memory.get("working_memory") or {})
    dominant = memory.get("dominant_type") or working.get("patterns", {}).get("dominant_type") or "mixed"
    if event.get("type") == "dtmf":
        sequence = str(event.get("sequence") or event.get("tone") or "").strip()
        context = str(event.get("context_summary") or "").strip()
        if context:
            return f"Control layer twitches: DTMF {sequence} near {context}."
        return f"Control layer twitches: DTMF {sequence} detected."
    if event.get("content_type") == "song":
        title = str(event.get("title") or "").strip()
        artist = str(event.get("artist") or "").strip()
        inferred = bool(event.get("inferred"))
        if title and artist:
            return f"Music drifts through: {'inferred ' if inferred else ''}{title} by {artist}."
        if title:
            return f"Music drifts through: {'inferred ' if inferred else ''}{title}."
        return f"Music drifts through: {summary}."
    if event.get("content_type") == "discussion_topic":
        topic = str(event.get("topic") or summary).strip()
        return f"Voices circle a topic: {topic}."
    if event.get("content_type") == "weather_report":
        return f"Weather rolls in: {summary}."
    if event.get("content_type") == "weather_advisory":
        return f"Forecast sharpens: {summary}."
    if event.get("anomaly"):
        return f"Air turns strange: {summary}. Pattern trend is {dominant}."
    expectation = dict(event.get("expectation") or {})
    if expectation.get("reason") == "repetition":
        return f"Band holds steady: {summary}. Pattern remains {dominant}."
    return f"Band log: {summary}. Recent pattern stays {dominant}."


def format_sol_log_line(event: dict[str, Any], narration: str) -> str:
    ts = event.get("ts") or "unknown"
    channels = ",".join(event.get("channels", [])) or "unknown"
    return f"{ts} [{channels}] {narration}".strip()
