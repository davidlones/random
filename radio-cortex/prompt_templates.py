from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = (
    "You are a radio signal interpreter. "
    "Return one compact JSON object only. No prose, no markdown, no code fences."
)


def _render_inputs(chunks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for chunk in chunks:
        ts = chunk.get("ts") or chunk.get("timestamp") or "unknown"
        channel = chunk.get("channel") or chunk.get("source_name") or "unknown"
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"[{ts}] channel={channel} text={json.dumps(text, ensure_ascii=True)}")
    return "\n".join(lines)


def _assumed_mode_block(chunks: list[dict[str, Any]], assumed_mode: str | None) -> str:
    mode = (assumed_mode or "").strip().lower()
    if not mode:
        modes = [str(chunk.get("radio_mode") or "").strip().lower() for chunk in chunks]
        modes = [item for item in modes if item]
        if modes and all(item == "weather" for item in modes):
            mode = "weather"
    if mode != "weather":
        return ""
    return (
        "Source context:\n"
        "- This batch is from weather mode / NOAA weather radio.\n"
        "- Default assumption: spoken forecast, advisory, station ID, or emergency content.\n"
        "- Do not reinterpret forecast phrasing as lyrics or music unless the transcript contains unmistakable non-weather evidence.\n\n"
    )


def classify_prompt(
    chunks: list[dict[str, Any]],
    *,
    allow_inference_details: bool = True,
    assumed_mode: str | None = None,
) -> str:
    detail_rules = (
        "- Only populate title/artist for songs when there is enough evidence; if guessed from lyrics or weak clues, set inferred=true.\n"
        "- If the text is spoken commentary, banter, interview, or topical discussion, prefer type=chatter and content_type=discussion_topic, and fill topic with a short noun phrase.\n"
        "- For discussion topics, summarize what is being discussed, not just that people are talking.\n"
        "- For song fragments, summary should say it is a song or lyric fragment when title is not known.\n"
    )
    if not allow_inference_details:
        detail_rules = (
            "- For music/song fragments, do not guess title or artist in this pass; leave title and artist empty unless explicitly spoken in the transcript.\n"
            "- For spoken banter/interview segments, do not infer a topic in this pass; leave topic empty unless explicitly stated in the transcript.\n"
            "- For ads, promos, or event announcements, do not infer entity, venue, date, or subtype in this pass unless the transcript states them clearly.\n"
            "- For chatter with no explicit topic phrase, keep the summary generic rather than inventing a topic.\n"
            "- For song fragments with no explicit identification, keep the summary generic rather than guessing the track name.\n"
        )
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"{_assumed_mode_block(chunks, assumed_mode)}"
        "Classify the radio activity below.\n"
        "JSON schema:\n"
        "{\n"
        '  "type": "weather|advisory|emergency|station_id|event|music|chatter|interference|unknown",\n'
        '  "content_type": "weather_report|weather_advisory|station_identification|song|discussion_topic|concert|promotion|commercial|interference|unknown",\n'
        '  "confidence": 0.0,\n'
        '  "summary": "one short sentence with a time reference or phase plus the key condition/risk",\n'
        '  "detailed_summary": "complete paraphrase that preserves all important details",\n'
        '  "anomaly": false,\n'
        '  "shared_event": false,\n'
        '  "channels": ["main"],\n'
        '  "reasons": ["brief reason"],\n'
        '  "title": "",\n'
        '  "artist": "",\n'
        '  "topic": "",\n'
        '  "entity": "",\n'
        '  "location": "",\n'
        '  "date": "",\n'
        '  "event_type": "",\n'
        '  "inferred": false\n'
        "}\n\n"
        "Rules:\n"
        "- If the text is obviously forecast/weather radio, type=weather.\n"
        "- For routine forecast or current-conditions content, set content_type=weather_report.\n"
        "- Station identification like NOAA/National Weather Service callouts should be type=station_id.\n"
        "- For station identification, set content_type=station_identification.\n"
        "- Routine fire weather or burning guidance should be type=advisory, not system.\n"
        "- For weather advisories, set content_type=weather_advisory.\n"
        "- For weather, advisory, and station identification content, detailed_summary must preserve all material details mentioned in the transcript.\n"
        "- If weather details are already clean in the transcript, detailed_summary may closely paraphrase or restate them, but must not omit times, locations, temperatures, winds, or warnings that are present.\n"
        "- If the text is mostly lyrics or a song fragment, type=music and content_type=song.\n"
        f"{detail_rules}"
        "- If the text contains a specific event with names, venue/place, date/time, tickets, sale, or announcement details, prefer type=event.\n"
        "- Concert/live-show ads should use content_type=concert; generic promos/ads can use promotion or commercial.\n"
        "- If it sounds like noise, clipping, broken decoder chatter, or RF garbage, type=interference.\n"
        "- If type=interference, set content_type=interference.\n"
        "- Only set anomaly=true for something operationally unusual.\n"
        "- Summary must not be one word.\n"
        "- Include useful scope when available: tonight, Friday, weekend, station ID, current conditions, or advisory.\n"
        "- For event summaries, include the entity and date or venue when present.\n"
        "- Do not invent keywords or facts not present in the transcript.\n"
        "- Keep confidence conservative.\n\n"
        f"Inputs:\n{_render_inputs(chunks)}\n"
    )


def inference_window_prompt(chunks: list[dict[str, Any]], *, assumed_mode: str | None = None) -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"{_assumed_mode_block(chunks, assumed_mode)}"
        "Look at the transcript window below and infer the current segment identity.\n"
        "This is a slower enrichment pass for chatter, music, ads, promos, and event announcements.\n"
        "If there is not enough evidence for a meaningful segment inference, return type=unknown and leave detail fields empty.\n"
        "JSON schema:\n"
        "{\n"
        '  "type": "weather|advisory|emergency|station_id|event|music|chatter|interference|unknown",\n'
        '  "content_type": "weather_report|weather_advisory|station_identification|song|discussion_topic|concert|promotion|commercial|interference|unknown",\n'
        '  "confidence": 0.0,\n'
        '  "summary": "discussion topic: ... | inferred song: ... | unknown",\n'
        '  "detailed_summary": "",\n'
        '  "anomaly": false,\n'
        '  "shared_event": false,\n'
        '  "channels": ["main"],\n'
        '  "reasons": ["brief reason"],\n'
        '  "title": "",\n'
        '  "artist": "",\n'
        '  "topic": "",\n'
        '  "entity": "",\n'
        '  "location": "",\n'
        '  "date": "",\n'
        '  "event_type": "",\n'
        '  "inferred": false\n'
        "}\n\n"
        "Rules:\n"
        "- Focus on chatter, music, ads, promos, and event-style content; do not spend this pass classifying weather or station ID.\n"
        "- If spoken commentary has a coherent subject, return type=chatter, content_type=discussion_topic, and fill topic with a short noun phrase.\n"
        "- If lyrics or DJ clues suggest a song, return type=music, content_type=song, and populate title and artist when possible.\n"
        "- If the window is clearly an ad, promo, sponsorship, or event announcement, use type=event with the best matching content_type such as concert, promotion, or commercial.\n"
        "- For ads/promos/events, fill entity, location, date, and event_type when the transcript supports them.\n"
        "- If title or artist is guessed from lyrics or indirect clues, set inferred=true.\n"
        "- If the evidence is weak or ambiguous, prefer unknown over bluffing.\n"
        "- Summary should mirror the inferred detail: discussion topic, inferred song, or concise ad/event summary.\n"
        "- Keep confidence conservative.\n\n"
        f"Inputs:\n{_render_inputs(chunks)}\n"
    )


def song_identification_prompt(chunks: list[dict[str, Any]], *, candidate_context: list[str] | None = None) -> str:
    candidate_block = ""
    if candidate_context:
        rendered = "\n".join(f"- {item}" for item in candidate_context if str(item).strip())
        if rendered:
            candidate_block = f"\nPossible station/music context:\n{rendered}\n"
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "Look at the transcript window below and identify the song if possible.\n"
        "Return one JSON object only with this schema:\n"
        "{\n"
        '  "title": "",\n'
        '  "artist": "",\n'
        '  "summary": "inferred song: ... | song fragment: ... | unknown",\n'
        '  "confidence": 0.0,\n'
        '  "inferred": false\n'
        "}\n\n"
        "Rules:\n"
        "- Use lyrics, repeated phrases, DJ cues, and nearby transcript context to guess the song title and artist.\n"
        "- If you are making a best-effort guess from lyrics rather than explicit metadata, set inferred=true.\n"
        "- If you cannot identify the song confidently enough to name it, leave title and artist empty and summarize the fragment briefly.\n"
        "- Do not invent a title unless it is a plausible best guess from the transcript.\n"
        "- Prefer using the candidate context only as a hint; lyrics in the current window should dominate.\n"
        "- Keep confidence conservative.\n\n"
        f"{candidate_block}"
        f"Inputs:\n{_render_inputs(chunks)}\n"
    )


def narration_prompt(event: dict[str, Any], memory: dict[str, Any]) -> str:
    return (
        "You are SOL, keeping a terse atmospheric log of radio activity.\n"
        "Return plain text only, one sentence, under 24 words.\n\n"
        f"Event JSON:\n{json.dumps(event, ensure_ascii=True, sort_keys=True)}\n\n"
        f"Memory JSON:\n{json.dumps(memory, ensure_ascii=True, sort_keys=True)}\n"
    )
