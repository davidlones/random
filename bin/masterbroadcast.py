#!/usr/bin/env python3
"""
Masterbroadcast: speak terminal/file text into one or more Discord voice channels.

Usage examples:
  python3 masterbroadcast.py --channels 123,456 --text "Hello world"
  python3 masterbroadcast.py --channels 123 --file /path/to/message.txt
  cat /path/to/message.txt | python3 masterbroadcast.py --channels 123 456
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import shelve
from pathlib import Path
from typing import Any, Dict, List, Tuple

import discord


DEFAULT_11SPEAK_PATH = Path("/home/david/random/bin/11speak.py")
FFMPEG_BEFORE_OPTIONS = "-nostdin"
DEFAULT_MASTERBOT_SERVICE = "masterbot.service"
MASTERBOT_RUNTIME_DB_PATH = Path("/home/david/random/logs/masterbot.db")
MASTERBROADCAST_CONSOLE_CACHE_PATH = Path("/home/david/random/logs/masterbroadcast_console_messages.json")
MASTERBOT_VOICE_PRESET_FALLBACK = [
    496375061134049298,
    631005215222661134,
    1072011626573729896,
    1476758309586468915,
]
MASTERBOT_TEXT_PRESET_FALLBACK = [
    496375061134049294,
    632804257317519370,
    1109584160047247420,
    631005215222661132,
    728359915504009369,
    330555280020471811,
]
VOICE_CONNECT_ATTEMPTS = 3
VOICE_CONNECT_RETRY_BASE_S = 1.0
SCRIPT_PRESETS = {
    "mock-emergency-flash": """[MOCK EMERGENCY BROADCAST TEST]
FLASH: EMERGENCY BROADCAST ACTIVE
This is a systems test only. No physical emergency is in progress.

FLASH: EMERGENCY BROADCAST STANDBY
Repeat: this is a coordinated test. Please do not panic.

FLASH: EMERGENCY BROADCAST ACTIVE
Operators are validating interrupt and resume behavior.

FLASH: EMERGENCY BROADCAST STANDBY
Voice channels are receiving synchronized spoken output.

FLASH: EMERGENCY BROADCAST ACTIVE
Text channels are receiving independent live-updating console messages.

FLASH: EMERGENCY BROADCAST STANDBY
End of test sequence approaching.

FLASH: EMERGENCY BROADCAST ACTIVE
Final verification marker.

FLASH: EMERGENCY BROADCAST STANDBY
Test complete. Returning to regularly scheduled programming.""",
}


def _parse_channel_ids(raw_values: List[str]) -> List[int]:
    out: List[int] = []
    for raw in raw_values:
        for part in str(raw).split(","):
            token = part.strip()
            if not token:
                continue
            try:
                out.append(int(token))
            except ValueError as exc:
                raise ValueError(f"Invalid channel id: {token!r}") from exc
    deduped: List[int] = []
    seen = set()
    for cid in out:
        if cid in seen:
            continue
        seen.add(cid)
        deduped.append(cid)
    return deduped


def _read_payload(args: argparse.Namespace) -> str:
    if args.script_preset:
        return str(SCRIPT_PRESETS.get(str(args.script_preset), "")).strip()
    if args.text:
        return args.text.strip()
    if args.file:
        return Path(args.file).expanduser().read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def _split_text_chunks(text: str, max_chars: int) -> List[str]:
    payload = (text or "").strip()
    if not payload:
        return []
    limit = max(80, int(max_chars or 600))
    if len(payload) <= limit:
        return [payload]

    chunks: List[str] = []
    paragraphs = [p.strip() for p in payload.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [payload]

    for para in paragraphs:
        if len(para) <= limit:
            chunks.append(para)
            continue
        words = para.split()
        current: List[str] = []
        current_len = 0
        for word in words:
            proposed = (current_len + 1 + len(word)) if current else len(word)
            if proposed <= limit:
                current.append(word)
                current_len = proposed
                continue
            if current:
                chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        if current:
            chunks.append(" ".join(current))
    return [c for c in chunks if c.strip()]


def _parse_console_message_refs(raw_values: List[str]) -> List[Tuple[int, int]]:
    refs: List[Tuple[int, int]] = []
    seen = set()
    for raw in raw_values:
        for part in str(raw).split(","):
            token = part.strip()
            if not token:
                continue
            if ":" not in token:
                raise ValueError(
                    f"Invalid console message ref: {token!r} (expected channel_id:message_id)"
                )
            channel_raw, message_raw = token.split(":", 1)
            try:
                ref = (int(channel_raw.strip()), int(message_raw.strip()))
            except ValueError as exc:
                raise ValueError(
                    f"Invalid console message ref: {token!r} (non-integer id)"
                ) from exc
            if ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
    return refs


def _parse_text_channel_ids(raw_values: List[str]) -> List[int]:
    out: List[int] = []
    seen = set()
    for raw in raw_values:
        for part in str(raw).split(","):
            token = part.strip()
            if not token:
                continue
            try:
                cid = int(token)
            except ValueError as exc:
                raise ValueError(f"Invalid text channel id: {token!r}") from exc
            if cid in seen:
                continue
            seen.add(cid)
            out.append(cid)
    return out


def _load_masterbot_runtime_channels() -> Dict[str, List[int]]:
    voice_ids = list(MASTERBOT_VOICE_PRESET_FALLBACK)
    text_ids = list(MASTERBOT_TEXT_PRESET_FALLBACK)
    try:
        if MASTERBOT_RUNTIME_DB_PATH.exists():
            with shelve.open(str(MASTERBOT_RUNTIME_DB_PATH)) as db:
                cfg = db.get("runtime_config", {})
                if isinstance(cfg, dict):
                    raw_voice = cfg.get("voice_loop_preset_channel_ids", [])
                    raw_text = cfg.get("server_room_status_channel_ids", [])
                    parsed_voice = [int(x) for x in raw_voice if str(x).strip()]
                    parsed_text = [int(x) for x in raw_text if str(x).strip()]
                    if parsed_voice:
                        voice_ids = parsed_voice
                    if parsed_text:
                        text_ids = parsed_text
    except Exception as exc:
        print(f"[masterbroadcast] WARN: failed loading runtime preset from {MASTERBOT_RUNTIME_DB_PATH}: {exc}")
    return {"voice_channels": voice_ids, "text_channels": text_ids}


def _load_console_cache() -> Dict[str, Any]:
    try:
        if MASTERBROADCAST_CONSOLE_CACHE_PATH.exists():
            obj = json.loads(MASTERBROADCAST_CONSOLE_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                return obj
    except Exception as exc:
        print(f"[masterbroadcast] WARN: failed reading console cache: {exc}")
    return {"channels": {}}


def _save_console_cache(cache: Dict[str, Any]) -> None:
    try:
        MASTERBROADCAST_CONSOLE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        MASTERBROADCAST_CONSOLE_CACHE_PATH.write_text(
            json.dumps(cache, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[masterbroadcast] WARN: failed writing console cache: {exc}")


def _is_compatible_console_message(content: str) -> bool:
    text = (content or "").strip()
    return "=== MASTERBROADCAST LIVE ===" in text and "now speaking:" in text


def _resolve_presets(presets: List[str]) -> Dict[str, List[int]]:
    resolved = {"voice_channels": [], "text_channels": []}
    for preset in presets:
        key = str(preset).strip().lower()
        if key == "masterbot-all":
            loaded = _load_masterbot_runtime_channels()
            resolved["voice_channels"].extend(loaded.get("voice_channels", []))
            resolved["text_channels"].extend(loaded.get("text_channels", []))
    # Deduplicate while preserving order.
    for bucket in ("voice_channels", "text_channels"):
        uniq: List[int] = []
        seen = set()
        for cid in resolved[bucket]:
            if cid in seen:
                continue
            seen.add(cid)
            uniq.append(cid)
        resolved[bucket] = uniq
    return resolved


def _synthesize_mp3(text: str, out_path: Path, args: argparse.Namespace) -> None:
    speak_bin = Path(args.speak_bin).expanduser()
    if not speak_bin.exists():
        raise FileNotFoundError(f"11speak script not found: {speak_bin}")
    cmd = [
        sys.executable,
        str(speak_bin),
        "--no-speaker",
        "--save-stream",
        str(out_path),
    ]
    if args.voice:
        cmd.extend(["--voice", str(args.voice)])
    if args.model:
        cmd.extend(["--model", str(args.model)])
    if args.chunk_chars:
        cmd.extend(["--chunk-chars", str(int(args.chunk_chars))])
    cmd.append("-")

    print(f"[masterbroadcast] synthesizing via: {' '.join(shlex.quote(x) for x in cmd)}")
    proc = subprocess.run(
        cmd,
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or "unknown synthesis error"
        raise RuntimeError(f"11speak failed: {detail}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("Synthesis completed but output MP3 is missing or empty.")


def _systemctl_user(action: str, service: str) -> Tuple[bool, str]:
    cmd = ["systemctl", "--user", action, service]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    detail = ((proc.stdout or "").strip() or (proc.stderr or "").strip() or f"exit={proc.returncode}")
    return (proc.returncode == 0), detail


async def _connect_voice_with_retry(
    target: discord.abc.Connectable,
    timeout_s: float,
    attempts: int = VOICE_CONNECT_ATTEMPTS,
) -> discord.VoiceClient:
    last_exc: Exception | None = None
    tries = max(1, int(attempts))
    for attempt in range(1, tries + 1):
        try:
            return await target.connect(timeout=timeout_s, reconnect=False)
        except Exception as exc:
            last_exc = exc
            if attempt >= tries:
                break
            # 4006 appears during transient voice websocket handshake churn.
            close_code = int(getattr(exc, "code", 0) or 0)
            wait_s = VOICE_CONNECT_RETRY_BASE_S * float(attempt)
            if close_code == 4006:
                print(
                    f"[masterbroadcast] WARN: voice connect retry {attempt}/{tries} "
                    f"for {target.id} after close code 4006; waiting {wait_s:.1f}s"
                )
            else:
                print(
                    f"[masterbroadcast] WARN: voice connect retry {attempt}/{tries} "
                    f"for {target.id}; waiting {wait_s:.1f}s ({exc})"
                )
            await asyncio.sleep(wait_s)
    if last_exc is None:
        raise RuntimeError(f"voice connect failed for {target.id}")
    raise last_exc


async def _voice_play_one(vc: discord.VoiceClient, media_path: Path) -> None:
    if not media_path.exists():
        raise FileNotFoundError(str(media_path))
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed or not in PATH")

    loop = asyncio.get_running_loop()
    done: asyncio.Future = loop.create_future()
    source = discord.FFmpegPCMAudio(str(media_path), before_options=FFMPEG_BEFORE_OPTIONS)

    def _after(err: Exception | None) -> None:
        if done.done():
            return
        loop.call_soon_threadsafe(done.set_result, err)

    vc.play(source, after=_after)
    err = await done
    if err:
        raise RuntimeError(str(err))


async def _play_to_target(target: discord.abc.Connectable, media_path: Path, timeout_s: float) -> None:
    vc: discord.VoiceClient | None = None
    try:
        vc = await target.connect(timeout=timeout_s, reconnect=False)
        await asyncio.wait_for(_voice_play_one(vc, media_path), timeout=max(timeout_s, 10.0) * 4)
    finally:
        if vc and vc.is_connected():
            await vc.disconnect(force=True)


async def _edit_console_messages(
    messages: List[discord.Message],
    chunk_text: str,
    chunk_index: int,
    chunk_total: int,
) -> None:
    if not messages:
        return
    body = chunk_text.strip().replace("```", "'''")
    if len(body) > 1500:
        body = body[:1497] + "..."
    content = (
        "=== MASTERBROADCAST LIVE ===\n"
        f"chunk: {chunk_index}/{chunk_total}\n"
        "now speaking:\n"
        f"```text\n{body}\n```"
    )
    tasks = [msg.edit(content=content) for msg in messages]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for msg, res in zip(messages, results):
        if isinstance(res, Exception):
            print(f"[masterbroadcast] WARN: failed editing console message {msg.id}: {res}")


async def _resolve_console_messages(
    client: discord.Client,
    refs: List[Tuple[int, int]],
) -> Tuple[List[discord.Message], List[str]]:
    out: List[discord.Message] = []
    errs: List[str] = []
    for channel_id, message_id in refs:
        channel = client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(channel_id)
            except Exception as exc:
                errs.append(f"console ref {channel_id}:{message_id} failed channel fetch: {exc}")
                continue
        fetch_fn = getattr(channel, "fetch_message", None)
        if fetch_fn is None:
            errs.append(
                f"console ref {channel_id}:{message_id} invalid channel type: {type(channel).__name__}"
            )
            continue
        try:
            msg = await fetch_fn(message_id)
            out.append(msg)
        except Exception as exc:
            errs.append(f"console ref {channel_id}:{message_id} failed message fetch: {exc}")
    return out, errs


async def _create_console_messages(
    client: discord.Client,
    channel_ids: List[int],
    reuse_last: bool,
) -> Tuple[List[discord.Message], List[str]]:
    out: List[discord.Message] = []
    errs: List[str] = []
    cache = _load_console_cache()
    channel_map = dict(cache.get("channels", {}))
    starter = (
        "=== MASTERBROADCAST LIVE ===\n"
        "chunk: 0/0\n"
        "now speaking:\n"
        "```text\nwaiting for broadcast...\n```"
    )
    for channel_id in channel_ids:
        channel = client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(channel_id)
            except Exception as exc:
                errs.append(f"console create channel {channel_id} failed fetch: {exc}")
                continue
        if not isinstance(channel, discord.TextChannel):
            errs.append(f"console create channel {channel_id} invalid type: {type(channel).__name__}")
            continue

        if reuse_last:
            cached_mid = int(channel_map.get(str(channel_id), 0) or 0)
            if cached_mid > 0:
                try:
                    cached_msg = await channel.fetch_message(cached_mid)
                    if (
                        client.user
                        and int(getattr(cached_msg.author, "id", 0) or 0) == int(client.user.id)
                        and _is_compatible_console_message(str(getattr(cached_msg, "content", "") or ""))
                    ):
                        out.append(cached_msg)
                        continue
                except Exception:
                    # Cache miss/stale message falls through to creating a new one.
                    pass
        try:
            msg = await channel.send(starter)
            out.append(msg)
            channel_map[str(channel_id)] = int(msg.id)
        except Exception as exc:
            errs.append(f"console create channel {channel_id} failed send: {exc}")
    cache["channels"] = channel_map
    _save_console_cache(cache)
    return out, errs


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Speak text/file content aloud to one or more Discord voice channels.",
    )
    parser.add_argument(
        "--channels",
        nargs="+",
        default=[],
        help="Voice/stage channel IDs. Accepts space-separated IDs and/or comma-separated groups.",
    )
    parser.add_argument(
        "--preset",
        nargs="+",
        default=[],
        choices=["masterbot-all"],
        help=(
            "Optional channel preset(s). "
            "'masterbot-all' uses masterbot runtime-configured 4 voice presets and status text channels."
        ),
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--text", help="Text to speak.")
    src.add_argument("--file", help="Path to a UTF-8 text file to speak.")
    src.add_argument(
        "--script-preset",
        choices=sorted(SCRIPT_PRESETS.keys()),
        help="Use a built-in broadcast script payload.",
    )
    parser.add_argument("--speak-bin", default=str(DEFAULT_11SPEAK_PATH), help="Path to 11speak.py")
    parser.add_argument("--voice", help="Optional ElevenLabs voice ID/name passed to 11speak.")
    parser.add_argument("--model", help="Optional ElevenLabs model passed to 11speak.")
    parser.add_argument("--chunk-chars", type=int, default=600, help="Chunk size passed to 11speak.")
    parser.add_argument("--timeout-s", type=float, default=20.0, help="Voice connect/play timeout budget.")
    parser.add_argument(
        "--console-messages",
        nargs="+",
        default=[],
        help=(
            "Optional live transcription message refs as channel_id:message_id. "
            "Accepts space-separated and/or comma-separated values."
        ),
    )
    parser.add_argument(
        "--console-create-channels",
        nargs="+",
        default=[],
        help=(
            "Optional text channel IDs where masterbroadcast should create a fresh "
            "console message and live-edit it. Accepts space/comma-separated IDs."
        ),
    )
    parser.add_argument(
        "--reuse-last-console-messages",
        action="store_true",
        help=(
            "When creating console messages, reuse last compatible masterbroadcast "
            "message per channel (if found) instead of always posting a new one."
        ),
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Play channels one at a time (default is parallel fanout).",
    )
    parser.add_argument(
        "--interrupt-masterbot",
        action="store_true",
        help="Stop masterbot daemon before broadcast and start it again afterward (default behavior).",
    )
    parser.add_argument(
        "--no-interrupt-masterbot",
        action="store_false",
        dest="interrupt_masterbot",
        help="Do not auto-stop/auto-start masterbot around this broadcast.",
    )
    parser.add_argument(
        "--stop-masterbot",
        action="store_true",
        help="Stop masterbot daemon before broadcast (no auto-resume unless --start-masterbot is also set).",
    )
    parser.add_argument(
        "--start-masterbot",
        action="store_true",
        help="Start masterbot daemon after broadcast completes.",
    )
    parser.add_argument(
        "--masterbot-service",
        default=DEFAULT_MASTERBOT_SERVICE,
        help=f"User systemd service name to control (default: {DEFAULT_MASTERBOT_SERVICE}).",
    )
    parser.set_defaults(interrupt_masterbot=True)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        print("[masterbroadcast] ERROR: DISCORD_TOKEN is not set.", file=sys.stderr)
        return 2
    if not os.getenv("ELEVENLABS_API_KEY", "").strip():
        print("[masterbroadcast] ERROR: ELEVENLABS_API_KEY is not set.", file=sys.stderr)
        return 2

    try:
        channel_ids = _parse_channel_ids(args.channels)
    except ValueError as exc:
        print(f"[masterbroadcast] ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        console_refs = _parse_console_message_refs(args.console_messages)
    except ValueError as exc:
        print(f"[masterbroadcast] ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        console_create_channels = _parse_text_channel_ids(args.console_create_channels)
    except ValueError as exc:
        print(f"[masterbroadcast] ERROR: {exc}", file=sys.stderr)
        return 2
    preset_ids = _resolve_presets(args.preset)
    if preset_ids["voice_channels"]:
        channel_ids.extend([cid for cid in preset_ids["voice_channels"] if cid not in channel_ids])
    if preset_ids["text_channels"]:
        console_create_channels.extend([cid for cid in preset_ids["text_channels"] if cid not in console_create_channels])
    if not channel_ids:
        print("[masterbroadcast] ERROR: no valid channel IDs provided. Supply --channels and/or --preset.", file=sys.stderr)
        return 2

    payload = _read_payload(args)
    if not payload:
        print(
            "[masterbroadcast] ERROR: no text input found. Use --text, --file, or pipe stdin.",
            file=sys.stderr,
        )
        return 2

    chunks = _split_text_chunks(payload, int(args.chunk_chars or 600))
    if not chunks:
        print("[masterbroadcast] ERROR: text was empty after normalization.", file=sys.stderr)
        return 2

    service_name = str(args.masterbot_service or DEFAULT_MASTERBOT_SERVICE).strip()
    should_stop = bool(args.interrupt_masterbot or args.stop_masterbot)
    should_start = bool(args.interrupt_masterbot or args.start_masterbot)
    masterbot_stopped = False

    if should_stop:
        ok, detail = _systemctl_user("stop", service_name)
        if ok:
            masterbot_stopped = True
            print(f"[masterbroadcast] stopped {service_name}")
        else:
            print(f"[masterbroadcast] WARN: failed stopping {service_name}: {detail}")

    try:
        with tempfile.TemporaryDirectory(prefix="masterbroadcast-") as td:
            intents = discord.Intents.none()
            intents.guilds = True
            intents.voice_states = True
            client = discord.Client(intents=intents)
            outcome = {"errors": [], "connected_targets": 0, "played_units": 0}

            @client.event
            async def on_ready() -> None:
                print(f"[masterbroadcast] connected as {client.user} | guilds={len(client.guilds)}")
                targets: List[discord.abc.Connectable] = []
                for cid in channel_ids:
                    ch = client.get_channel(cid)
                    if ch is None:
                        outcome["errors"].append(f"channel not found: {cid}")
                        continue
                    if not isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                        outcome["errors"].append(f"not voice/stage channel: {cid} ({type(ch).__name__})")
                        continue
                    targets.append(ch)

                if not targets:
                    outcome["errors"].append("no valid targets to play.")
                    await client.close()
                    return

                console_messages, console_errors = await _resolve_console_messages(client, console_refs)
                outcome["errors"].extend(console_errors)
                created_console_messages, created_console_errors = await _create_console_messages(
                    client,
                    console_create_channels,
                    reuse_last=bool(args.reuse_last_console_messages),
                )
                outcome["errors"].extend(created_console_errors)
                console_messages.extend(created_console_messages)
                if console_messages:
                    print(
                        "[masterbroadcast] console mirrors: "
                        + ", ".join(f"{m.channel.id}:{m.id}" for m in console_messages)
                    )

                print(
                    "[masterbroadcast] targets: "
                    + ", ".join(f"{t.guild.name}/{t.name}({t.id})" for t in targets)
                )

                voice_clients: Dict[int, discord.VoiceClient] = {}
                connected_target_ids: set[int] = set()
                try:
                    for target in targets:
                        print(f"[masterbroadcast] connecting -> {target.guild.name}/{target.name}")
                        try:
                            vc = await _connect_voice_with_retry(target, timeout_s=float(args.timeout_s))
                            voice_clients[int(target.id)] = vc
                            connected_target_ids.add(int(target.id))
                        except Exception as exc:
                            outcome["errors"].append(f"{target.id}: connect failed: {exc}")
                    if not voice_clients:
                        outcome["errors"].append("no voice connections established.")
                        return

                    for idx, chunk in enumerate(chunks, start=1):
                        out_mp3 = Path(td) / f"broadcast_chunk_{idx:03d}.mp3"
                        try:
                            _synthesize_mp3(chunk, out_mp3, args)
                        except Exception as exc:
                            outcome["errors"].append(f"chunk {idx}: synthesis failed: {exc}")
                            continue

                        await _edit_console_messages(console_messages, chunk, idx, len(chunks))

                        active_pairs: List[Tuple[discord.abc.Connectable, discord.VoiceClient]] = []
                        for target in targets:
                            current_vc = voice_clients.get(int(target.id))
                            if current_vc is None or not current_vc.is_connected():
                                with contextlib.suppress(Exception):
                                    if current_vc and current_vc.is_connected():
                                        await current_vc.disconnect(force=True)
                                try:
                                    reconnected = await _connect_voice_with_retry(
                                        target, timeout_s=float(args.timeout_s)
                                    )
                                    voice_clients[int(target.id)] = reconnected
                                    connected_target_ids.add(int(target.id))
                                    current_vc = reconnected
                                except Exception as exc:
                                    outcome["errors"].append(
                                        f"{target.id}: chunk {idx}: reconnect failed: {exc}"
                                    )
                                    continue
                            active_pairs.append((target, current_vc))

                        if not active_pairs:
                            outcome["errors"].append(f"chunk {idx}: no connected voice targets available.")
                            continue

                        if args.sequential:
                            for target, vc in active_pairs:
                                print(
                                    f"[masterbroadcast] chunk {idx}/{len(chunks)} -> "
                                    f"{target.guild.name}/{target.name}"
                                )
                                try:
                                    await asyncio.wait_for(
                                        _voice_play_one(vc, out_mp3),
                                        timeout=max(float(args.timeout_s), 10.0) * 4,
                                    )
                                    outcome["played_units"] = int(outcome.get("played_units", 0) or 0) + 1
                                except Exception as exc:
                                    outcome["errors"].append(f"{target.id}: chunk {idx}: {exc}")
                        else:
                            async def _run_pair(t: discord.abc.Connectable, v: discord.VoiceClient) -> None:
                                print(
                                    f"[masterbroadcast] chunk {idx}/{len(chunks)} -> "
                                    f"{t.guild.name}/{t.name}"
                                )
                                await asyncio.wait_for(
                                    _voice_play_one(v, out_mp3),
                                    timeout=max(float(args.timeout_s), 10.0) * 4,
                                )

                            results = await asyncio.gather(
                                *[_run_pair(target, vc) for target, vc in active_pairs],
                                return_exceptions=True,
                            )
                            for (target, _), res in zip(active_pairs, results):
                                if isinstance(res, Exception):
                                    outcome["errors"].append(f"{target.id}: chunk {idx}: {res}")
                                else:
                                    outcome["played_units"] = int(outcome.get("played_units", 0) or 0) + 1
                    outcome["connected_targets"] = len(connected_target_ids)
                finally:
                    for vc in list(voice_clients.values()):
                        with contextlib.suppress(Exception):
                            if vc.is_connected():
                                await vc.disconnect(force=True)
                    await client.close()

            try:
                client.run(token)
            except KeyboardInterrupt:
                print("[masterbroadcast] interrupted.")
                return 130
            except Exception as exc:
                print(f"[masterbroadcast] ERROR: discord runtime failed: {exc}", file=sys.stderr)
                return 1

            if outcome["errors"]:
                print("[masterbroadcast] completed with warnings/errors:")
                for line in outcome["errors"]:
                    print(f"  - {line}")
                connected_targets = int(outcome.get("connected_targets", 0) or 0)
                played_units = int(outcome.get("played_units", 0) or 0)
                if connected_targets > 0 and played_units > 0:
                    print(
                        "[masterbroadcast] proceeding despite warnings: "
                        f"connected_targets={connected_targets} played_units={played_units}"
                    )
                    return 0
                return 1

            print("[masterbroadcast] broadcast complete.")
            return 0
    finally:
        if should_start and (masterbot_stopped or args.start_masterbot):
            ok, detail = _systemctl_user("start", service_name)
            if ok:
                print(f"[masterbroadcast] started {service_name}")
            else:
                print(f"[masterbroadcast] WARN: failed starting {service_name}: {detail}")


if __name__ == "__main__":
    raise SystemExit(main())
