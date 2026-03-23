from __future__ import annotations

import json
import re
from typing import Any


NOISE_PATTERNS = (
    r"\bavailable demodulators\b",
    r"\bmultimon-ng\b",
    r"\bdecoder\b",
    r"\bunparsed transcriber output\b",
)


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


def normalize_text(value: Any) -> str:
    cleaned = clean_text(value).lower()
    return re.sub(r"[^a-z0-9\s]+", " ", cleaned).strip()


def looks_like_noise(text: str, min_chars: int) -> bool:
    cleaned = clean_text(text)
    if len(cleaned) < min_chars:
        return True
    lower = cleaned.lower()
    if any(re.search(pattern, lower) for pattern in NOISE_PATTERNS):
        return True
    alpha = sum(char.isalpha() for char in cleaned)
    return alpha < max(6, len(cleaned) // 5)


def extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        raise ValueError("empty model response")
    decoder = json.JSONDecoder()
    last_good: dict[str, Any] | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            last_good = parsed
    if last_good is None:
        raise ValueError(f"no JSON object found in model response: {text[:200]}")
    return last_good


def extract_grounded_keywords(texts: list[str], event_type: str) -> list[str]:
    joined = normalize_text(" ".join(texts))
    if not joined:
        return []
    keywords: list[str] = []
    seen: set[str] = set()
    weather_terms = (
        "warning",
        "watch",
        "advisory",
        "forecast",
        "tonight",
        "today",
        "tomorrow",
        "weekend",
        "thunderstorm",
        "tornado",
        "flood",
        "rain",
        "wind",
        "gust",
        "clear",
        "sunny",
        "cloudy",
        "fire weather",
        "burning",
    )
    station_terms = (
        "noaa",
        "national weather service",
        "all hazards radio",
        "station",
        "kec55",
    )
    for term in weather_terms + station_terms:
        if term in joined and term not in seen:
            keywords.append(term)
            seen.add(term)
    if event_type == "weather" and "forecast" not in seen and any(word in joined for word in ("tonight", "today", "tomorrow", "friday", "saturday", "sunday")):
        keywords.append("forecast")
    return keywords[:8]


def summarize_phase(texts: list[str]) -> str | None:
    joined = normalize_text(" ".join(texts))
    if not joined:
        return None
    if "all hazards radio" in joined or "national weather service" in joined or "you are listening" in joined:
        return "station_id"
    if any(term in joined for term in ("warning", "watch", "advisory", "fire weather", "avoid outdoor burning")):
        return "advisory"
    if any(term in joined for term in ("temperature", "dew point", "humidity", "pressure", "airport", "around the region")):
        return "current_conditions"
    if any(term in joined for term in ("forecast", "tonight", "today", "tomorrow", "friday", "saturday", "sunday", "monday")):
        return "forecast"
    return "general"
