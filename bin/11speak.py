#!/usr/bin/env python3

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid

from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError


DEFAULT_VOICE_ID = "Ae8FqfxSWOTMNHeqtdUQ"
DEFAULT_MODEL_ID = "eleven_flash_v2_5"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
DEFAULT_CHUNK_CHARS = 700


def load_text(args: argparse.Namespace) -> str:
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            return f.read()

    if args.text and args.text != ["-"]:
        return " ".join(args.text)

    if not sys.stdin.isatty():
        return sys.stdin.read()

    sys.exit("No text provided. Pass text, use --file, or pipe text on stdin.")


def split_long_unit(text: str, limit: int) -> list[str]:
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(word) > limit:
            for i in range(0, len(word), limit):
                chunks.append(word[i : i + limit])
            current = ""
        else:
            current = word
    if current:
        chunks.append(current)
    return chunks


def chunk_text(text: str, limit: int) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    parts = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        if not part:
            continue
        if len(part) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(split_long_unit(part, limit))
            continue
        candidate = part if not current else f"{current} {part}"
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = part
    if current:
        chunks.append(current)
    return chunks


def api_error_message(err: ApiError) -> str:
    body = getattr(err, "body", None)
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        status = detail.get("status")
        message = detail.get("message")
        if status == "quota_exceeded":
            return f"ElevenLabs quota exceeded: {message}"
        if message:
            return f"ElevenLabs API error ({status or err.status_code}): {message}"
    if body:
        try:
            body_text = json.dumps(body)
        except Exception:
            body_text = str(body)
        return f"ElevenLabs API error ({err.status_code}): {body_text}"
    return f"ElevenLabs API error ({err.status_code})"


def now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


class TelemetryEmitter:
    def __init__(
        self,
        *,
        path: str | None,
        voice_id: str,
        model_id: str,
        output_format: str,
        chunk_chars: int,
        dialogue_mode: bool,
    ) -> None:
        self.path = path
        self.handle = None
        self.enabled = bool(path)
        self.session_id = str(uuid.uuid4())
        self.session_start_mono = time.monotonic()
        self.global_audio_byte_offset = 0
        self.utterance_seq = 0

        if not self.enabled:
            return

        if path == "-":
            self.handle = sys.stdout
        else:
            self.handle = open(path, "a", encoding="utf-8")

        self.emit(
            "session_start",
            voice_id=voice_id,
            model_id=model_id,
            output_format=output_format,
            chunk_chars=chunk_chars,
            dialogue_mode=dialogue_mode,
            pid=os.getpid(),
        )

    def emit(self, event: str, **data: object) -> None:
        if not self.enabled or self.handle is None:
            return
        record = {
            "event": event,
            "session_id": self.session_id,
            "ts_unix": time.time(),
            "ts_iso": now_iso_utc(),
            "t_session_ms": round((time.monotonic() - self.session_start_mono) * 1000, 3),
        }
        record.update(data)
        self.handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        self.handle.flush()

    def next_utterance(self) -> int:
        self.utterance_seq += 1
        return self.utterance_seq

    def reserve_bytes(self, n: int) -> tuple[int, int]:
        start = self.global_audio_byte_offset
        self.global_audio_byte_offset += n
        return start, self.global_audio_byte_offset

    def close(self) -> None:
        if not self.enabled:
            return
        self.emit(
            "session_end",
            total_utterances=self.utterance_seq,
            total_audio_bytes=self.global_audio_byte_offset,
        )
        if self.handle not in (None, sys.stdout):
            self.handle.close()
        self.handle = None


class AudioFanout:
    def __init__(
        self,
        *,
        enable_speaker: bool = True,
        save_stream: str | None = None,
        tee_cmd: str | None = None,
    ) -> None:
        self.enable_speaker = enable_speaker
        self.save_stream_path = save_stream
        self.tee_cmd = tee_cmd
        self.player: subprocess.Popen | None = None
        self.tee_proc: subprocess.Popen | None = None
        self.file_handle = None

        if self.save_stream_path:
            self.file_handle = open(self.save_stream_path, "wb")
        if self.tee_cmd:
            self.tee_proc = subprocess.Popen(
                shlex.split(self.tee_cmd),
                stdin=subprocess.PIPE,
            )
        if self.enable_speaker:
            self.start_player()

    def start_player(self) -> None:
        self.player = subprocess.Popen(
            [
                "ffplay",
                "-loglevel",
                "quiet",
                "-nodisp",
                "-autoexit",
                "-",
            ],
            stdin=subprocess.PIPE,
        )

    def stop_player(self, *, force: bool = True) -> None:
        if not self.player:
            return
        try:
            if self.player.stdin:
                self.player.stdin.close()
        except Exception:
            pass
        if self.player.poll() is None and force:
            self.player.terminate()
        try:
            self.player.wait(timeout=1 if force else 30)
        except Exception:
            if self.player.poll() is None:
                self.player.kill()
                self.player.wait()
        self.player = None

    def restart_player(self) -> None:
        if not self.enable_speaker:
            return
        self.stop_player(force=True)
        self.start_player()

    def write(self, data: bytes) -> None:
        sinks: list[tuple[str, object]] = []
        if self.player and self.player.stdin:
            sinks.append(("speaker", self.player.stdin))
        if self.file_handle:
            sinks.append(("file", self.file_handle))
        if self.tee_proc and self.tee_proc.stdin:
            sinks.append(("tee", self.tee_proc.stdin))

        for name, sink in sinks:
            try:
                sink.write(data)
                if name != "speaker":
                    sink.flush()
            except BrokenPipeError:
                if name == "speaker":
                    self.player = None
                elif name == "tee":
                    self.tee_proc = None

    def close(self) -> None:
        # Graceful shutdown: let ffplay drain buffered audio before exit.
        self.stop_player(force=False)

        if self.tee_proc:
            try:
                if self.tee_proc.stdin:
                    self.tee_proc.stdin.close()
            except Exception:
                pass
            self.tee_proc.wait()
            self.tee_proc = None

        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None


def speak_text(
    client: ElevenLabs,
    fanout: AudioFanout,
    text: str,
    *,
    utterance_index: int,
    voice_id: str,
    model_id: str,
    chunk_chars: int,
    telemetry: TelemetryEmitter | None = None,
    verbose: bool = True,
) -> bool:
    chunks = chunk_text(text, chunk_chars)
    if not chunks:
        print("No non-whitespace text to speak", file=sys.stderr)
        return True

    utter_start_mono = time.monotonic()
    utter_bytes = 0
    if telemetry:
        telemetry.emit(
            "utterance_start",
            utterance_index=utterance_index,
            input_chars=len(text),
            chunk_count=len(chunks),
            byte_offset_start=telemetry.global_audio_byte_offset,
            preview=text[:120],
        )

    try:
        if verbose and len(chunks) > 1:
            print(
                f"Speaking {len(chunks)} chunks (~{chunk_chars} chars each)...",
                file=sys.stderr,
            )
        for index, text_chunk in enumerate(chunks, start=1):
            chunk_start_mono = time.monotonic()
            chunk_byte_start_global = telemetry.global_audio_byte_offset if telemetry else None
            chunk_byte_start_utter = utter_bytes
            packet_count = 0
            chunk_audio_bytes = 0
            first_audio_ms = None
            if verbose and len(chunks) > 1:
                print(
                    f"Chunk {index}/{len(chunks)} ({len(text_chunk)} chars)",
                    file=sys.stderr,
                )
            if telemetry:
                telemetry.emit(
                    "chunk_start",
                    utterance_index=utterance_index,
                    chunk_index=index,
                    chunk_count=len(chunks),
                    text_chars=len(text_chunk),
                    text_preview=text_chunk[:120],
                    byte_offset_start=chunk_byte_start_global,
                    utterance_byte_offset_start=chunk_byte_start_utter,
                )
            audio_stream = client.text_to_speech.convert(
                text=text_chunk,
                voice_id=voice_id,
                model_id=model_id,
                output_format=DEFAULT_OUTPUT_FORMAT,
            )
            for audio_chunk in audio_stream:
                if first_audio_ms is None:
                    first_audio_ms = round((time.monotonic() - chunk_start_mono) * 1000, 3)
                packet_count += 1
                chunk_audio_bytes += len(audio_chunk)
                utter_bytes += len(audio_chunk)
                if telemetry:
                    _, global_end = telemetry.reserve_bytes(len(audio_chunk))
                fanout.write(audio_chunk)
            if telemetry:
                telemetry.emit(
                    "chunk_end",
                    utterance_index=utterance_index,
                    chunk_index=index,
                    chunk_count=len(chunks),
                    text_chars=len(text_chunk),
                    audio_packets=packet_count,
                    audio_bytes=chunk_audio_bytes,
                    byte_offset_start=chunk_byte_start_global,
                    byte_offset_end=(chunk_byte_start_global + chunk_audio_bytes),
                    utterance_byte_offset_start=chunk_byte_start_utter,
                    utterance_byte_offset_end=(chunk_byte_start_utter + chunk_audio_bytes),
                    ttfb_ms=first_audio_ms,
                    chunk_elapsed_ms=round((time.monotonic() - chunk_start_mono) * 1000, 3),
                )
        if telemetry:
            telemetry.emit(
                "utterance_end",
                utterance_index=utterance_index,
                success=True,
                audio_bytes=utter_bytes,
                byte_offset_end=telemetry.global_audio_byte_offset,
                utterance_elapsed_ms=round((time.monotonic() - utter_start_mono) * 1000, 3),
            )
        return True
    except KeyboardInterrupt:
        print("\nSpeech interrupted (Ctrl-C). Restarting player.", file=sys.stderr)
        if telemetry:
            telemetry.emit(
                "utterance_end",
                utterance_index=utterance_index,
                success=False,
                interrupted=True,
                audio_bytes=utter_bytes,
                byte_offset_end=telemetry.global_audio_byte_offset,
                utterance_elapsed_ms=round((time.monotonic() - utter_start_mono) * 1000, 3),
            )
        fanout.restart_player()
        return False
    except ApiError as e:
        print(api_error_message(e), file=sys.stderr)
        if telemetry:
            telemetry.emit(
                "utterance_end",
                utterance_index=utterance_index,
                success=False,
                api_error=True,
                error=api_error_message(e),
                audio_bytes=utter_bytes,
                byte_offset_end=telemetry.global_audio_byte_offset,
                utterance_elapsed_ms=round((time.monotonic() - utter_start_mono) * 1000, 3),
            )
        return False


def dry_run_report(text: str, chunk_chars: int) -> int:
    chunks = chunk_text(text, chunk_chars)
    if not chunks:
        print("No non-whitespace text to speak", file=sys.stderr)
        return 1
    print(f"Input chars: {len(text)}")
    print(f"Chunks: {len(chunks)}")
    for i, c in enumerate(chunks, start=1):
        preview = c[:60].replace("\n", " ")
        suffix = "..." if len(c) > 60 else ""
        print(f"{i:>3}: {len(c)} chars | {preview}{suffix}")
    return 0


def run_dialogue_mode(args: argparse.Namespace, client: ElevenLabs) -> int:
    if sys.stdin.isatty():
        print("Dialogue mode. Enter text to speak. Ctrl-C interrupts current speech.", file=sys.stderr)
        print("Commands: /quit, /exit, /stop", file=sys.stderr)

    fanout = AudioFanout(
        enable_speaker=not args.no_speaker,
        save_stream=args.save_stream,
        tee_cmd=args.tee_cmd,
    )
    telemetry = TelemetryEmitter(
        path=args.telemetry_file,
        voice_id=args.voice_id,
        model_id=args.model_id,
        output_format=DEFAULT_OUTPUT_FORMAT,
        chunk_chars=args.chunk_chars,
        dialogue_mode=True,
    )

    try:
        while True:
            try:
                if sys.stdin.isatty():
                    line = input("11speak> ")
                else:
                    line = sys.stdin.readline()
                    if line == "":
                        break
            except EOFError:
                break
            except KeyboardInterrupt:
                print(file=sys.stderr)
                continue

            text = line.strip()
            if not text:
                continue
            if text in {"/quit", "/exit"}:
                telemetry.emit("dialogue_command", command=text)
                break
            if text == "/stop":
                telemetry.emit("dialogue_command", command=text)
                fanout.restart_player()
                continue

            utterance_index = telemetry.next_utterance()
            speak_text(
                client,
                fanout,
                text,
                utterance_index=utterance_index,
                voice_id=args.voice_id,
                model_id=args.model_id,
                chunk_chars=args.chunk_chars,
                telemetry=telemetry,
            )
    finally:
        fanout.close()
        telemetry.close()

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "text",
        nargs="*",
        help="Text to speak. Use '-' or omit to read from stdin.",
    )
    parser.add_argument(
        "--file",
        help="Read text from a file instead of argv/stdin.",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=DEFAULT_CHUNK_CHARS,
        help=f"Chunk long input into requests of roughly this many characters (default: {DEFAULT_CHUNK_CHARS}).",
    )
    parser.add_argument("--voice-id", default=DEFAULT_VOICE_ID)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse input and show chunking info without calling ElevenLabs or playing audio.",
    )
    parser.add_argument(
        "--dialogue",
        action="store_true",
        help="Keep the process (and ffplay) open and read multiple lines for continuous dialogue.",
    )
    parser.add_argument(
        "--save-stream",
        help="Write the raw streamed MP3 bytes to this file while playing.",
    )
    parser.add_argument(
        "--tee-cmd",
        help="Also pipe the raw MP3 stream to another command (e.g. a visualizer pipeline).",
    )
    parser.add_argument(
        "--no-speaker",
        action="store_true",
        help="Do not play audio locally; only tee/save it.",
    )
    parser.add_argument(
        "--telemetry-file",
        help="Append NDJSON timing/byte telemetry to this file ('-' for stdout).",
    )
    args = parser.parse_args()
    if args.chunk_chars < 50:
        sys.exit("--chunk-chars must be at least 50")
    return args


def main() -> int:
    args = parse_args()

    if args.dry_run and args.dialogue:
        sys.exit("--dry-run and --dialogue are mutually exclusive")

    if args.dry_run:
        return dry_run_report(load_text(args), args.chunk_chars)

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        sys.exit("ELEVENLABS_API_KEY not set")
    client = ElevenLabs(api_key=api_key)

    if args.dialogue:
        return run_dialogue_mode(args, client)

    text = load_text(args)
    fanout = AudioFanout(
        enable_speaker=not args.no_speaker,
        save_stream=args.save_stream,
        tee_cmd=args.tee_cmd,
    )
    telemetry = TelemetryEmitter(
        path=args.telemetry_file,
        voice_id=args.voice_id,
        model_id=args.model_id,
        output_format=DEFAULT_OUTPUT_FORMAT,
        chunk_chars=args.chunk_chars,
        dialogue_mode=False,
    )
    try:
        utterance_index = telemetry.next_utterance()
        ok = speak_text(
            client,
            fanout,
            text,
            utterance_index=utterance_index,
            voice_id=args.voice_id,
            model_id=args.model_id,
            chunk_chars=args.chunk_chars,
            telemetry=telemetry,
        )
        return 0 if ok else 1
    finally:
        fanout.close()
        telemetry.close()


if __name__ == "__main__":
    sys.exit(main())
