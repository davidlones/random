from __future__ import annotations

from typing import Any

from utils.time import now_iso
from utils.filters import clean_text, extract_grounded_keywords, summarize_phase


ALLOWED_TYPES = {
    "weather",
    "advisory",
    "emergency",
    "station_id",
    "event",
    "music",
    "chatter",
    "interference",
    "unknown",
}

ALLOWED_CONTENT_TYPES = {
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
}

SUMMARY_GARBAGE_FRAGMENTS = (
    "one short sentence with a time reference or phase plus the key condition/risk",
    "time reference or phase plus the key condition/risk",
    "tonight, friday, weekend, station id, current conditions, or advisory",
)

VERBATIM_TYPES = {"weather", "advisory", "station_id"}

WEATHER_STATION_TERMS = (
    "national weather service",
    "all hazards radio",
    "current conditions",
    "dew point",
    "humidity",
    "pressure",
    "miles an hour",
    "extended forecast",
    "forecast",
    "south wind",
    "north wind",
    "east wind",
    "west wind",
)

WEATHER_GENERAL_TERMS = (
    "tonight",
    "today",
    "tomorrow",
    "friday",
    "saturday",
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "sunny",
    "clear",
    "cloudy",
    "partly cloudy",
    "mostly cloudy",
    "mostly sunny",
    "rain",
    "showers",
    "thunderstorm",
    "temperature",
    "lows",
    "highs",
    "degrees",
    "humidity",
    "wind",
    "gusts",
)


def _count_terms(joined: str, terms: tuple[str, ...]) -> int:
    return sum(1 for term in terms if term in joined)


def _looks_like_lyric_lines(texts: list[str]) -> bool:
    if not texts:
        return False
    short_lines = 0
    for text in texts[:8]:
        words = clean_text(text).split()
        if 3 <= len(words) <= 12:
            short_lines += 1
    return short_lines >= max(2, min(len(texts), 3))


def _looks_like_weather_report(joined: str) -> bool:
    if not joined:
        return False
    if "all hazards radio" in joined or "national weather service" in joined:
        return True
    station_hits = _count_terms(joined, WEATHER_STATION_TERMS)
    general_hits = _count_terms(joined, WEATHER_GENERAL_TERMS)
    if "extended forecast" in joined:
        return True
    if "miles an hour" in joined and any(term in joined for term in ("wind", "north", "south", "east", "west")):
        return True
    if any(term in joined for term in ("highs", "lows")) and any(term in joined for term in ("sunny", "clear", "cloudy", "rain", "showers", "thunderstorm", "degrees")):
        return True
    if "degrees" in joined and any(term in joined for term in ("temperature", "highs", "lows", "humidity", "wind")):
        return True
    if station_hits >= 2:
        return True
    if station_hits >= 1 and general_hits >= 1:
        return True
    if general_hits >= 4:
        return True
    return False


def _heuristic_type(texts: list[str]) -> str:
    joined = " ".join(texts).lower()
    if not joined:
        return "unknown"
    if any(term in joined for term in ("available demodulators", "multimon-ng", "decoder")):
        return "interference"
    if "all hazards radio" in joined or "national weather service" in joined or "you are listening" in joined:
        return "station_id"
    if any(term in joined for term in ("warning", "take cover", "evacuate", "tornado emergency")):
        return "emergency"
    if any(term in joined for term in ("advisory", "fire weather", "avoid outdoor burning", "red flag")):
        return "advisory"
    if any(term in joined for term in ("tickets", "on september", "on october", "at dos equis", "festival", "concert", "live at", "visit ")) and any(char.isdigit() for char in joined):
        return "event"
    if any(term in joined for term in ("chorus", "verse", "feat.", "featuring", "lyrics")):
        return "music"
    if _looks_like_weather_report(joined):
        return "weather"
    if _looks_like_lyric_lines(texts):
        return "music"
    if any(term in joined for term in ("talking about", "we're talking", "discussion", "interview", "host", "welcome back")):
        return "chatter"
    return "unknown"


def _normalize_type(value: Any, texts: list[str]) -> str:
    raw = str(value or "unknown").strip().lower()
    joined = " ".join(texts).lower()
    if raw == "system":
        raw = "unknown"
    if raw == "unknown":
        return _heuristic_type(texts)
    if "all hazards radio" in joined or "national weather service" in joined:
        return "station_id"
    if raw in {"music", "chatter", "unknown"} and _looks_like_weather_report(joined):
        return "weather"
    if raw == "weather" and any(term in joined for term in ("fire weather", "avoid outdoor burning", "red flag")):
        return "advisory"
    if raw not in ALLOWED_TYPES:
        return _heuristic_type(texts)
    return raw


def _sanitize_summary(value: Any) -> str:
    summary = clean_text(value)
    lower = summary.lower()
    for fragment in SUMMARY_GARBAGE_FRAGMENTS:
        idx = lower.find(fragment)
        if idx >= 0:
            summary = clean_text(summary[:idx])
            lower = summary.lower()
    if summary.startswith('"') and summary.endswith('"'):
        summary = summary[1:-1].strip()
    return summary


def _normalize_summary(value: Any, event_type: str, texts: list[str]) -> str:
    summary = _sanitize_summary(value)
    if len(summary.split()) >= 3:
        return summary
    phase = summarize_phase(texts)
    combined = clean_text(" ".join(texts))
    if not combined:
        return event_type
    sentences = [part.strip(" .") for part in combined.split(".") if part.strip()]
    lead = sentences[0] if sentences else combined
    if phase == "station_id":
        return "station identification from NOAA weather radio"
    if phase == "advisory":
        return f"weather advisory: {lead[:140]}"
    if phase == "current_conditions":
        return f"current conditions: {lead[:140]}"
    if phase == "forecast":
        return f"weather report: {lead[:144]}"
    if event_type == "event":
        return lead[:160]
    if event_type == "music":
        return "song or lyric fragment on air"
    if event_type == "chatter":
        return f"discussion topic: {lead[:136]}"
    if event_type == "interference":
        return "radio interference or decoder contamination detected"
    return lead[:160]


def _compose_full_text(texts: list[str]) -> str:
    parts: list[str] = []
    last_normalized = ""
    for text in texts:
        cleaned = clean_text(text)
        if not cleaned:
            continue
        normalized = " ".join(cleaned.lower().split())
        if normalized == last_normalized:
            continue
        parts.append(cleaned.rstrip(" ."))
        last_normalized = normalized
    if not parts:
        return ""
    return ". ".join(parts)


def _normalize_detailed_summary(value: Any, event_type: str, texts: list[str], summary: str) -> str | None:
    detailed = _sanitize_summary(value)
    full_text = _compose_full_text(texts)
    if event_type in VERBATIM_TYPES:
        if detailed and len(detailed.split()) >= max(8, len(summary.split())):
            return detailed
        return full_text or summary
    if detailed and len(detailed.split()) >= 6:
        return detailed
    return None


def _normalize_confidence(value: Any, parsed_type: Any, event_type: str, texts: list[str]) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))
    raw_type = str(parsed_type or "unknown").strip().lower()
    heuristic_type = _heuristic_type(texts)
    if event_type != raw_type and event_type == heuristic_type:
        return max(confidence, 0.5)
    return confidence


def _normalize_field(value: Any) -> str | None:
    text = clean_text(value)
    return text or None


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _normalize_content_type(value: Any, event_type: str, texts: list[str], event_subtype: str | None) -> str:
    raw = str(value or "").strip().lower()
    joined = " ".join(texts).lower()
    if raw in ALLOWED_CONTENT_TYPES:
        return raw
    if event_type == "weather":
        return "weather_report"
    if event_type == "advisory":
        return "weather_advisory"
    if event_type == "station_id":
        return "station_identification"
    if event_type == "music":
        return "song"
    if event_type == "chatter":
        return "discussion_topic"
    if event_type == "interference":
        return "interference"
    if event_type == "event":
        if event_subtype and "concert" in event_subtype.lower():
            return "concert"
        if any(term in joined for term in ("tickets", "concert", "festival", "live at", "dos equis")):
            return "concert"
        if any(term in joined for term in ("sale", "discount", "buy now", "sponsored")):
            return "commercial"
        return "promotion"
    return "unknown"


def _normalize_radio_mode(originals: list[dict[str, Any]]) -> str | None:
    modes = []
    for item in originals:
        mode = clean_text(item.get("radio_mode"))
        if mode:
            modes.append(mode.lower())
    if not modes:
        return None
    if all(mode == "weather" for mode in modes):
        return "weather"
    return modes[-1]


def _weather_mode_default_type(texts: list[str]) -> str:
    joined = " ".join(texts).lower()
    if not joined:
        return "weather"
    if any(term in joined for term in ("all hazards radio", "national weather service", "you are listening", "the current time is")):
        return "station_id"
    if any(term in joined for term in ("warning", "take cover", "evacuate", "tornado emergency")):
        return "emergency"
    if any(term in joined for term in ("advisory", "fire weather", "avoid outdoor burning", "red flag", "spotter information statement")):
        return "advisory"
    return "weather"


def _derive_topic(event_type: str, content_type: str, summary: str, texts: list[str]) -> str | None:
    if content_type == "discussion_topic":
        return summary.replace("discussion topic:", "").strip(" .") or None
    if content_type in {"concert", "promotion", "commercial"}:
        cleaned = summary.replace("discussion topic:", "").strip(" .")
        return cleaned or None
    if content_type == "song":
        cleaned = summary.replace("song:", "").replace("inferred song:", "").strip(" .")
        return cleaned or "song fragment"
    if event_type == "weather":
        phase = summarize_phase(texts)
        if phase == "forecast":
            return "forecast"
        if phase == "current_conditions":
            return "current conditions"
        return "weather report"
    if event_type == "advisory":
        return "weather advisory"
    return None


def process_event(parsed: dict[str, Any], originals: list[dict[str, Any]]) -> dict[str, Any]:
    channels = sorted({str(item.get("channel") or item.get("source_name") or "unknown") for item in originals})
    source_paths = sorted({str(item.get("_path") or "") for item in originals if item.get("_path")})
    timestamps = [str(item.get("ts") or item.get("timestamp") or "") for item in originals if item.get("ts") or item.get("timestamp")]
    texts = [clean_text(item.get("text")) for item in originals if clean_text(item.get("text"))]
    radio_mode = _normalize_radio_mode(originals)
    event_type = _normalize_type(parsed.get("type"), texts)
    if radio_mode == "weather" and event_type in {"music", "chatter", "unknown"}:
        event_type = _weather_mode_default_type(texts)
    keywords = extract_grounded_keywords(texts, event_type)
    summary = _normalize_summary(parsed.get("summary"), event_type, texts)
    full_text = _compose_full_text(texts)
    detailed_summary = _normalize_detailed_summary(parsed.get("detailed_summary"), event_type, texts, summary)
    confidence = _normalize_confidence(parsed.get("confidence"), parsed.get("type"), event_type, texts)
    title = _normalize_field(parsed.get("title"))
    artist = _normalize_field(parsed.get("artist"))
    topic = _normalize_field(parsed.get("topic"))
    entity = _normalize_field(parsed.get("entity"))
    location = _normalize_field(parsed.get("location"))
    event_subtype = _normalize_field(parsed.get("event_type"))
    event_date = _normalize_field(parsed.get("date"))
    content_type = _normalize_content_type(parsed.get("content_type"), event_type, texts, event_subtype)
    if topic is None:
        topic = _derive_topic(event_type, content_type, summary, texts)
    inferred = _normalize_bool(parsed.get("inferred"))
    if event_type == "music" and title and not summary.lower().startswith("inferred song"):
        prefix = "inferred song" if inferred else "song"
        summary = f"{prefix}: {title}"
        if artist:
            summary = f"{summary} by {artist}"
    elif event_type == "weather" and not summary.lower().startswith("weather report:") and content_type == "weather_report":
        summary = f"weather report: {summary}"
    elif event_type == "advisory" and not summary.lower().startswith("weather advisory:") and content_type == "weather_advisory":
        summary = f"weather advisory: {summary}"
    elif event_type == "chatter" and topic and "discussion topic:" not in summary.lower():
        summary = f"discussion topic: {topic}"
    return {
        "ts": now_iso(),
        "window_start": min(timestamps) if timestamps else None,
        "window_end": max(timestamps) if timestamps else None,
        "channels": channels,
        "source_paths": source_paths,
        "radio_mode": radio_mode,
        "type": event_type,
        "content_type": content_type,
        "confidence": confidence,
        "summary": summary,
        "detailed_summary": detailed_summary,
        "full_text": full_text or None,
        "anomaly": bool(parsed.get("anomaly")),
        "shared_event": bool(parsed.get("shared_event")),
        "keywords": keywords,
        "reasons": [str(reason) for reason in parsed.get("reasons", []) if str(reason).strip()],
        "phase": summarize_phase(texts),
        "title": title,
        "artist": artist,
        "topic": topic,
        "entity": entity,
        "location": location,
        "date": event_date,
        "event_type": event_subtype,
        "inferred": inferred,
        "items": [
            {
                "ts": item.get("ts") or item.get("timestamp"),
                "channel": item.get("channel") or item.get("source_name"),
                "text": item.get("text"),
            }
            for item in originals
        ],
    }
