#!/usr/bin/env python3
from __future__ import annotations

import argparse
import audioop
import io
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any, Optional

import vosk
from openai import OpenAI
from radio_config import load_radio_config


CONFIG = load_radio_config()
TRANSCRIBE_CONFIG = CONFIG["transcribe"]
DEFAULT_NEMO_RUNTIME = str(Path(TRANSCRIBE_CONFIG["nemo_runtime_python"]).expanduser())


def get_gqrx_sink_input() -> Optional[tuple[str, str]]:
    try:
        output = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    current_index: Optional[str] = None
    app_name = ""
    app_binary = ""
    media_name = ""
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if raw_line.startswith("Sink Input #"):
            if current_index and (app_name == "GQRX" or app_binary == "gqrx"):
                label = media_name or app_name or app_binary or "gqrx"
                return current_index, label
            current_index = raw_line.split("#", 1)[1].strip()
            app_name = ""
            app_binary = ""
            media_name = ""
            continue
        if line.startswith("application.name ="):
            app_name = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("application.process.binary ="):
            app_binary = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("media.name ="):
            media_name = line.split("=", 1)[1].strip().strip('"')
    if current_index and (app_name == "GQRX" or app_binary == "gqrx"):
        label = media_name or app_name or app_binary or "gqrx"
        return current_index, label
    return None


def get_default_monitor_source() -> Optional[str]:
    try:
        info = subprocess.run(
            ["pactl", "info"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    default_sink = None
    for line in info.splitlines():
        if line.startswith("Default Sink:"):
            default_sink = line.split(":", 1)[1].strip()
            break
    if not default_sink:
        return None
    return f"{default_sink}.monitor"


def get_default_capture_target() -> Optional[str]:
    sink_input = get_gqrx_sink_input()
    if sink_input is not None:
        return f"sink-input:{sink_input[0]}"
    return get_default_monitor_source()


def emit(event: str, text: str, json_mode: bool) -> None:
    if not text.strip():
        return
    if json_mode:
        print(json.dumps({"event": event, "text": text}, ensure_ascii=True), flush=True)
        return
    print(f"{event}: {text}", flush=True)


def emit_backend_status(backend: str, model: str, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"event": "backend_status", "backend": backend, "model": model}, ensure_ascii=True), flush=True)
        return
    print(f"backend: {backend} ({model})", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live speech transcription from the current radio audio.")
    parser.add_argument(
        "--source",
        help="PulseAudio monitor source or sink-input target like sink-input:3321. Defaults to the live GQRX stream when available, else the default sink monitor.",
    )
    parser.add_argument(
        "--input-file",
        help="Optional raw PCM input file or FIFO. Use '-' for stdin. Overrides PulseAudio capture.",
    )
    parser.add_argument(
        "--input-format",
        choices=("s16le",),
        default="s16le",
        help="Raw PCM input format for --input-file. Defaults to s16le.",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=int(TRANSCRIBE_CONFIG["sample_rate"]),
        help="Recognition sample rate in Hz. Defaults to 16000.",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "vosk", "openai", "nemo"),
        default=str(TRANSCRIBE_CONFIG["backend"]),
        help="Transcription backend. 'auto' prefers NeMo when available, then OpenAI, then Vosk.",
    )
    parser.add_argument(
        "--all-local",
        action="store_true",
        default=bool(TRANSCRIBE_CONFIG["all_local"]),
        help="Force local transcription only. Prefers NeMo when available, then falls back to Vosk.",
    )
    parser.add_argument(
        "--lang",
        default=str(TRANSCRIBE_CONFIG["lang"]),
        help="Language hint. Defaults to en-us.",
    )
    parser.add_argument(
        "--model",
        default=TRANSCRIBE_CONFIG["model"],
        help="Optional local Vosk model path.",
    )
    parser.add_argument(
        "--model-name",
        default=TRANSCRIBE_CONFIG["model_name"],
        help="Optional Vosk model name override.",
    )
    parser.add_argument(
        "--openai-model",
        default=str(TRANSCRIBE_CONFIG["openai_model"]),
        help="OpenAI transcription model to use when backend=openai.",
    )
    parser.add_argument(
        "--nemo-model",
        default=str(TRANSCRIBE_CONFIG["nemo_model"]),
        help="NeMo model to use when backend=nemo. Canary models are also supported by name.",
    )
    parser.add_argument(
        "--nemo-device",
        choices=("auto", "cuda", "cpu"),
        default=str(TRANSCRIBE_CONFIG["nemo_device"]),
        help="Device preference for NeMo transcription.",
    )
    parser.add_argument(
        "--nemo-runtime-python",
        default=str(TRANSCRIBE_CONFIG["nemo_runtime_python"]),
        help="Optional Python interpreter with torch+nemo installed for the NeMo backend.",
    )
    parser.add_argument(
        "--prompt",
        default=str(TRANSCRIBE_CONFIG["prompt"]),
        help="Optional transcription prompt or vocabulary hint.",
    )
    parser.add_argument(
        "--chunk-seconds",
        type=float,
        default=float(TRANSCRIBE_CONFIG["chunk_seconds"]),
        help="Chunk length for the OpenAI backend in seconds.",
    )
    parser.add_argument(
        "--min-chunk-seconds",
        type=float,
        default=float(TRANSCRIBE_CONFIG["min_chunk_seconds"]),
        help="Minimum buffered audio length to transcribe on shutdown.",
    )
    parser.add_argument(
        "--min-rms",
        type=int,
        default=int(TRANSCRIBE_CONFIG["min_rms"]),
        help="Skip very quiet chunks below this RMS threshold in the OpenAI backend.",
    )
    parser.add_argument(
        "--partials",
        action="store_true",
        default=bool(TRANSCRIBE_CONFIG["partials"]),
        help="Print rolling partial hypotheses as speech arrives. Only supported by the Vosk backend.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON lines instead of plain text.",
    )
    parser.add_argument(
        "--min-partial-chars",
        type=int,
        default=int(TRANSCRIBE_CONFIG["min_partial_chars"]),
        help="Suppress tiny partial fragments shorter than this length.",
    )
    return parser.parse_args()


def choose_backend(args: argparse.Namespace) -> str:
    if args.all_local:
        if nemo_runtime_available(args):
            return "nemo"
        return "vosk"
    if args.backend != "auto":
        return args.backend
    if nemo_runtime_available(args):
        return "nemo"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "vosk"


def module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def runtime_has_nemo(runtime: str) -> bool:
    runtime_path = Path(runtime)
    if not runtime or not runtime_path.exists():
        return False
    probe = (
        "import importlib.util, sys; "
        "sys.exit(0 if importlib.util.find_spec('torch') and importlib.util.find_spec('nemo.collections.asr') else 1)"
    )
    result = subprocess.run([runtime, "-c", probe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def nemo_runtime_available(args: argparse.Namespace) -> bool:
    if module_exists("torch") and module_exists("nemo.collections.asr"):
        return True
    runtime = (args.nemo_runtime_python or "").strip()
    return runtime_has_nemo(runtime)


def maybe_reexec_for_nemo(args: argparse.Namespace) -> None:
    if args.backend != "nemo" and choose_backend(args) != "nemo":
        return
    if module_exists("torch") and module_exists("nemo.collections.asr"):
        return
    runtime = (args.nemo_runtime_python or "").strip()
    if not runtime:
        raise SystemExit("nemo backend requested, but no NeMo runtime is configured")
    if not Path(runtime).exists():
        raise SystemExit(f"nemo backend requested, but runtime python does not exist: {runtime}")
    if not runtime_has_nemo(runtime):
        raise SystemExit(f"nemo backend requested, but runtime is missing torch/NeMo: {runtime}")
    if os.environ.get("RADIO_TRANSCRIBE_NEMO_REEXEC") == "1":
        raise SystemExit("nemo backend requested, but the configured runtime still lacks torch/NeMo")
    env = os.environ.copy()
    env["RADIO_TRANSCRIBE_NEMO_REEXEC"] = "1"
    os.execvpe(runtime, [runtime, __file__, *sys.argv[1:]], env)


def normalize_openai_language(lang: str) -> str:
    value = (lang or "").strip().lower()
    if not value:
        return ""
    return value.split("-", 1)[0]


def backend_model_label(args: argparse.Namespace, backend: str) -> str:
    if backend == "openai":
        return args.openai_model
    if backend == "nemo":
        return args.nemo_model
    if args.model:
        return args.model
    if args.model_name:
        return args.model_name
    return f"vosk:{args.lang}"


def open_parec(source: str, sample_rate: int) -> subprocess.Popen[bytes]:
    parec_cmd = ["parec"]
    if source.startswith("sink-input:"):
        parec_cmd.extend(["--monitor-stream", source.split(":", 1)[1]])
    else:
        parec_cmd.extend(["--device", source])
    parec_cmd.extend(
        [
            "--format=s16le",
            "--rate",
            str(sample_rate),
            "--channels=1",
            "--latency-msec=100",
        ]
    )
    try:
        return subprocess.Popen(parec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise SystemExit(f"failed to start parec: {exc}") from exc


def open_input_stream(args: argparse.Namespace, source: str) -> tuple[subprocess.Popen[bytes] | None, Any]:
    if args.input_file:
        if args.input_file == "-":
            return None, sys.stdin.buffer
        return None, open(args.input_file, "rb", buffering=0)
    proc = open_parec(source, args.sample_rate)
    assert proc.stdout is not None
    return proc, proc.stdout


def load_vosk_model(args: argparse.Namespace) -> vosk.Model:
    if args.model:
        return vosk.Model(model_path=args.model)
    if args.model_name:
        return vosk.Model(model_name=args.model_name)
    return vosk.Model(lang=args.lang)


def transcribe_with_vosk(args: argparse.Namespace, source: str) -> int:
    vosk.SetLogLevel(-1)
    model = load_vosk_model(args)
    recognizer = vosk.KaldiRecognizer(model, args.sample_rate)
    recognizer.SetWords(True)
    proc, stream = open_input_stream(args, source)
    stop_requested = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stop_requested.set()
        if proc is not None and proc.poll() is None:
            proc.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    last_partial = ""
    try:
        while True:
            chunk = stream.read(4000)
            if not chunk:
                break
            if recognizer.AcceptWaveform(chunk):
                result = json.loads(recognizer.Result())
                text = (result.get("text") or "").strip()
                if text:
                    emit("final", text, args.json)
                last_partial = ""
                continue

            if not args.partials:
                continue
            partial = json.loads(recognizer.PartialResult()).get("partial", "").strip()
            if len(partial) < args.min_partial_chars or partial == last_partial:
                continue
            emit("partial", partial, args.json)
            last_partial = partial
            if stop_requested.is_set():
                break

        final_text = json.loads(recognizer.FinalResult()).get("text", "").strip()
        if final_text:
            emit("final", final_text, args.json)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
        if args.input_file and args.input_file != "-":
            stream.close()
    return 0


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int) -> io.BytesIO:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    buf.seek(0)
    buf.name = "radio.wav"
    return buf


def write_pcm_to_wav_path(pcm: bytes, sample_rate: int) -> str:
    with tempfile.NamedTemporaryFile(prefix="radio-nemo-", suffix=".wav", delete=False) as handle:
        path = handle.name
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return path


def maybe_transcribe_openai_chunk(
    client: OpenAI,
    pcm: bytes,
    args: argparse.Namespace,
    *,
    last_text: str,
) -> str:
    if not pcm:
        return last_text
    if audioop.rms(pcm, 2) < args.min_rms:
        return last_text

    wav_file = pcm_to_wav_bytes(pcm, args.sample_rate)
    request: dict[str, object] = {
        "file": wav_file,
        "model": args.openai_model,
        "response_format": "text",
        "temperature": 0,
    }
    language = normalize_openai_language(args.lang)
    if language:
        request["language"] = language
    if args.prompt:
        request["prompt"] = args.prompt

    try:
        result = client.audio.transcriptions.create(**request)
    except Exception as exc:
        emit("error", f"openai transcription failed: {exc}", args.json)
        return last_text

    if isinstance(result, str):
        text = result.strip()
    else:
        text = str(getattr(result, "text", "")).strip()
    if not text or text == last_text:
        return last_text
    emit("final", text, args.json)
    return text


def transcribe_with_openai(args: argparse.Namespace, source: str) -> int:
    if args.partials:
        emit("info", "partials are only available with the vosk backend", args.json)

    client = OpenAI()
    proc, stream = open_input_stream(args, source)
    stop_requested = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stop_requested.set()
        if proc is not None and proc.poll() is None:
            proc.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    chunk_bytes = max(1, int(args.sample_rate * 2 * args.chunk_seconds))
    min_chunk_bytes = max(1, int(args.sample_rate * 2 * args.min_chunk_seconds))
    buffer = bytearray()
    last_text = ""

    try:
        while True:
            chunk = stream.read(4000)
            if not chunk:
                break
            buffer.extend(chunk)
            while len(buffer) >= chunk_bytes:
                current = bytes(buffer[:chunk_bytes])
                del buffer[:chunk_bytes]
                last_text = maybe_transcribe_openai_chunk(client, current, args, last_text=last_text)
            if stop_requested.is_set():
                break

        if len(buffer) >= min_chunk_bytes:
            last_text = maybe_transcribe_openai_chunk(client, bytes(buffer), args, last_text=last_text)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
        if args.input_file and args.input_file != "-":
            stream.close()
    return 0


def choose_nemo_device(args: argparse.Namespace, torch: Any) -> str:
    if args.nemo_device == "cpu":
        return "cpu"
    if args.nemo_device == "cuda":
        if not torch.cuda.is_available():
            raise SystemExit("nemo backend requested with --nemo-device cuda, but CUDA is unavailable")
        return "cuda"
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_nemo_model(args: argparse.Namespace) -> tuple[Any, str]:
    try:
        import torch
        import nemo.collections.asr as nemo_asr
        from nemo.collections.asr.models import EncDecMultiTaskModel
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "nemo backend requires torch and nemo_toolkit[asr] in the active runtime or --nemo-runtime-python"
        ) from exc

    device = choose_nemo_device(args, torch)
    model_name = args.nemo_model.strip()
    if "canary" in model_name.lower():
        model = EncDecMultiTaskModel.from_pretrained(model_name=model_name)
        decode_cfg = model.cfg.decoding
        if hasattr(decode_cfg, "beam") and hasattr(decode_cfg.beam, "beam_size"):
            decode_cfg.beam.beam_size = 1
            model.change_decoding_strategy(decode_cfg)
        model_type = "canary"
    else:
        model = nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
        model_type = "asr"
    model.eval()
    model.to(device)
    return model, model_type


def extract_nemo_text(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text.strip()
    if isinstance(result, dict):
        maybe_text = result.get("text")
        if isinstance(maybe_text, str):
            return maybe_text.strip()
    return str(result).strip()


def transcribe_nemo_chunk(model: Any, model_type: str, wav_path: str, args: argparse.Namespace) -> str:
    if model_type == "canary":
        with tempfile.NamedTemporaryFile(prefix="radio-canary-", suffix=".jsonl", mode="w", encoding="utf-8", delete=False) as manifest:
            manifest_path = manifest.name
            json.dump(
                {
                    "audio_filepath": wav_path,
                    "duration": None,
                    "taskname": "asr",
                    "source_lang": "en",
                    "target_lang": "en",
                    "pnc": "yes",
                },
                manifest,
                ensure_ascii=True,
            )
            manifest.write("\n")
        try:
            result = model.transcribe(manifest_path, batch_size=1)
        finally:
            try:
                os.unlink(manifest_path)
            except FileNotFoundError:
                pass
    else:
        result = model.transcribe(audio=[wav_path], batch_size=1)
    if isinstance(result, list) and result:
        return extract_nemo_text(result[0])
    return extract_nemo_text(result)


def maybe_transcribe_nemo_chunk(model: Any, model_type: str, pcm: bytes, args: argparse.Namespace, *, last_text: str) -> str:
    if not pcm:
        return last_text
    if audioop.rms(pcm, 2) < args.min_rms:
        return last_text

    wav_path = write_pcm_to_wav_path(pcm, args.sample_rate)
    try:
        text = transcribe_nemo_chunk(model, model_type, wav_path, args)
    except Exception as exc:
        emit("error", f"nemo transcription failed: {exc}", args.json)
        return last_text
    finally:
        try:
            os.unlink(wav_path)
        except FileNotFoundError:
            pass

    if not text or text == last_text:
        return last_text
    emit("final", text, args.json)
    return text


def transcribe_with_nemo(args: argparse.Namespace, source: str) -> int:
    if args.partials:
        emit("info", "partials are only available with the vosk backend", args.json)

    model, model_type = load_nemo_model(args)
    proc, stream = open_input_stream(args, source)
    stop_requested = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stop_requested.set()
        if proc is not None and proc.poll() is None:
            proc.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    chunk_bytes = max(1, int(args.sample_rate * 2 * args.chunk_seconds))
    min_chunk_bytes = max(1, int(args.sample_rate * 2 * args.min_chunk_seconds))
    buffer = bytearray()
    last_text = ""

    try:
        while True:
            chunk = stream.read(4000)
            if not chunk:
                break
            buffer.extend(chunk)
            while len(buffer) >= chunk_bytes:
                current = bytes(buffer[:chunk_bytes])
                del buffer[:chunk_bytes]
                last_text = maybe_transcribe_nemo_chunk(model, model_type, current, args, last_text=last_text)
            if stop_requested.is_set():
                break

        if len(buffer) >= min_chunk_bytes:
            last_text = maybe_transcribe_nemo_chunk(model, model_type, bytes(buffer), args, last_text=last_text)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
        if args.input_file and args.input_file != "-":
            stream.close()
    return 0


def main() -> int:
    args = parse_args()
    maybe_reexec_for_nemo(args)
    source = args.source or get_default_capture_target()
    if not source:
        raise SystemExit("could not determine radio audio capture target")

    backend = choose_backend(args)
    emit_backend_status(backend, backend_model_label(args, backend), args.json)
    if backend == "nemo":
        return transcribe_with_nemo(args, source)
    if backend == "openai":
        return transcribe_with_openai(args, source)
    return transcribe_with_vosk(args, source)


if __name__ == "__main__":
    raise SystemExit(main())
