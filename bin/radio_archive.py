#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from radio_config import expand_path, load_radio_config

CONFIG = load_radio_config()
PATHS_CONFIG = CONFIG["paths"]
ARCHIVE_DEFAULTS = CONFIG["archive"]
BASE_DIR = Path(expand_path(PATHS_CONFIG["state_dir"])) / "archive"
JOBS_DIR = BASE_DIR / "jobs"
TRANSCRIBE_SCRIPT = Path(__file__).with_name("radio_transcribe.py")
SESSION_SCRIPT = Path(__file__).with_name("radio_session.py")
REMOTE_HOST = str(PATHS_CONFIG["remote_host"])
REMOTE_PORT = int(PATHS_CONFIG["remote_port"])


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


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


def is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def remote_command(command: str, timeout: float = 1.0) -> Optional[str]:
    try:
        with socket.create_connection((REMOTE_HOST, REMOTE_PORT), timeout=timeout) as sock:
            sock.sendall((command + "\n").encode())
            data = sock.recv(4096)
    except OSError:
        return None
    if not data:
        return None
    return data.decode(errors="replace").strip()


def session_command(*args: str) -> None:
    subprocess.run([sys.executable, str(SESSION_SCRIPT), *args], check=True, stdout=subprocess.DEVNULL)


@dataclass
class ArchiveConfig:
    name: str
    source: str
    cache_hours: float
    segment_seconds: int
    permanent: bool
    transcribe: bool
    transcribe_backend: str
    transcribe_model: str
    transcribe_prompt: str
    sample_rate: int
    metadata_poll_seconds: float
    language: str


class ArchiveWorker:
    def __init__(self, config: ArchiveConfig, job_dir: Path) -> None:
        self.config = config
        self.job_dir = job_dir
        self.audio_dir = self.job_dir / "audio"
        self.events_path = self.job_dir / "events.jsonl"
        self.transcripts_path = self.job_dir / "transcripts.jsonl"
        self.status_path = self.job_dir / "status.json"
        self.pid_path = self.job_dir / "pid"
        self.log_path = self.job_dir / "worker.log"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ffmpeg_proc: Optional[subprocess.Popen[str]] = None
        self._transcribe_proc: Optional[subprocess.Popen[str]] = None
        self._last_remote_snapshot: Optional[dict[str, Any]] = None
        self._status: dict[str, Any] = {
            "name": config.name,
            "started_at": now_iso(),
            "pid": os.getpid(),
            "source": config.source,
            "audio_dir": str(self.audio_dir),
            "events_path": str(self.events_path),
            "transcripts_path": str(self.transcripts_path),
            "transcribe": config.transcribe,
            "transcribe_backend": config.transcribe_backend,
            "transcribe_model": config.transcribe_model,
            "permanent": config.permanent,
            "cache_hours": config.cache_hours,
            "segment_seconds": config.segment_seconds,
            "segments": 0,
            "last_segment": None,
            "last_transcript_at": None,
            "last_transcript_text": None,
        }

    def write_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")

    def write_event(self, event: str, **fields: Any) -> None:
        self.write_jsonl(
            self.events_path,
            {
                "ts": now_iso(),
                "event": event,
                **fields,
            },
        )

    def write_status(self) -> None:
        self.status_path.write_text(json.dumps(self._status, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def update_segment_status(self) -> None:
        segments = sorted(self.audio_dir.glob("*.flac"))
        self._status["segments"] = len(segments)
        self._status["last_segment"] = str(segments[-1]) if segments else None
        self.write_status()

    def start_ffmpeg(self) -> None:
        pattern = str(self.audio_dir / "%Y%m%d-%H%M%S.flac")
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "pulse",
            "-i",
            self.config.source,
            "-ac",
            "1",
            "-ar",
            str(self.config.sample_rate),
            "-c:a",
            "flac",
            "-f",
            "segment",
            "-segment_time",
            str(self.config.segment_seconds),
            "-strftime",
            "1",
            pattern,
        ]
        self.write_event("ffmpeg_start", cmd=cmd)
        self._ffmpeg_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        def stderr_reader() -> None:
            assert self._ffmpeg_proc is not None
            assert self._ffmpeg_proc.stderr is not None
            for raw_line in self._ffmpeg_proc.stderr:
                line = raw_line.strip()
                if line:
                    self.write_event("ffmpeg_log", line=line)

        threading.Thread(target=stderr_reader, daemon=True).start()

    def start_transcriber(self) -> None:
        if not self.config.transcribe:
            return
        cmd = [
            sys.executable,
            str(TRANSCRIBE_SCRIPT),
            "--source",
            self.config.source,
            "--sample-rate",
            str(self.config.sample_rate),
            "--backend",
            self.config.transcribe_backend,
            "--lang",
            self.config.language,
            "--json",
        ]
        if self.config.transcribe_backend == "openai":
            cmd.extend(["--openai-model", self.config.transcribe_model])
        elif self.config.transcribe_backend == "nemo":
            cmd.extend(["--nemo-model", self.config.transcribe_model])
        if self.config.transcribe_prompt:
            cmd.extend(["--prompt", self.config.transcribe_prompt])
        self.write_event("transcriber_start", cmd=cmd)
        self._transcribe_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def transcript_reader() -> None:
            assert self._transcribe_proc is not None
            assert self._transcribe_proc.stdout is not None
            for raw_line in self._transcribe_proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    self.write_event("transcriber_log", line=line)
                    continue
                record = {
                    "ts": now_iso(),
                    "backend": self.config.transcribe_backend,
                    "model": self.config.transcribe_model,
                    **payload,
                }
                self.write_jsonl(self.transcripts_path, record)
                if payload.get("event") == "final" and payload.get("text"):
                    self._status["last_transcript_at"] = record["ts"]
                    self._status["last_transcript_text"] = payload["text"]
                    self.write_event("transcript_final", text=payload["text"])
                    self.write_status()

        threading.Thread(target=transcript_reader, daemon=True).start()

    def poll_remote_metadata(self) -> None:
        snapshot = {
            "version": remote_command("_"),
            "freq_hz": remote_command("f"),
            "mode": remote_command("m"),
            "signal_dbfs": remote_command("l STRENGTH"),
            "squelch_dbfs": remote_command("l SQL"),
            "dsp": remote_command("u DSP"),
            "rds": remote_command("u RDS"),
        }
        if all(value is None for value in snapshot.values()):
            return
        if snapshot != self._last_remote_snapshot:
            self.write_event("radio_state", **snapshot)
            self._last_remote_snapshot = snapshot

    def enforce_retention(self) -> None:
        if self.config.permanent:
            return
        cutoff = time.time() - (self.config.cache_hours * 3600)
        for path in sorted(self.audio_dir.glob("*.flac")):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
            except FileNotFoundError:
                continue
            try:
                path.unlink()
                self.write_event("segment_deleted", path=str(path), reason="cache_expired")
            except FileNotFoundError:
                continue

    def run(self) -> int:
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        session_command(
            "register-aux",
            "--backend",
            "archive",
            "--owner",
            self.config.name,
            "--pid",
            str(os.getpid()),
            "--detail",
            f"source={self.config.source}",
            "--detail",
            f"job_dir={self.job_dir}",
        )
        (self.job_dir / "config.json").write_text(
            json.dumps(asdict(self.config), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.write_event("session_start", **asdict(self.config))
        self.write_status()

        def stop(_signum: int, _frame: object) -> None:
            self._stop.set()

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

        self.start_ffmpeg()
        self.start_transcriber()

        try:
            last_segment_scan = 0.0
            last_retention = 0.0
            last_metadata = 0.0
            while not self._stop.is_set():
                now = time.time()
                if self._ffmpeg_proc is not None and self._ffmpeg_proc.poll() is not None:
                    self.write_event("ffmpeg_exit", returncode=self._ffmpeg_proc.returncode)
                    break
                if self._transcribe_proc is not None and self._transcribe_proc.poll() is not None:
                    self.write_event("transcriber_exit", returncode=self._transcribe_proc.returncode)
                    self._transcribe_proc = None

                if now - last_segment_scan >= 5:
                    self.update_segment_status()
                    last_segment_scan = now
                if now - last_retention >= 30:
                    self.enforce_retention()
                    last_retention = now
                if now - last_metadata >= self.config.metadata_poll_seconds:
                    self.poll_remote_metadata()
                    last_metadata = now
                time.sleep(0.5)
        finally:
            if self._transcribe_proc and self._transcribe_proc.poll() is None:
                self._transcribe_proc.terminate()
                try:
                    self._transcribe_proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    self.write_event("transcriber_force_kill")
                    self._transcribe_proc.kill()
            if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
                self._ffmpeg_proc.terminate()
                try:
                    self._ffmpeg_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.write_event("ffmpeg_force_kill")
                    self._ffmpeg_proc.kill()
            self.update_segment_status()
            self.write_event("session_stop")
            self._status["stopped_at"] = now_iso()
            self.write_status()
            try:
                session_command("release", "--pid", str(os.getpid()))
            except subprocess.CalledProcessError:
                pass
            try:
                self.pid_path.unlink()
            except FileNotFoundError:
                pass
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive radio audio with rolling retention and transcript sidecars.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="Start a named archive job in the background.")
    start.add_argument("name", help="Archive job name.")
    start.add_argument("--source", help="PulseAudio source/monitor to record.")
    start.add_argument("--cache-hours", type=float, default=float(ARCHIVE_DEFAULTS["cache_hours"]), help="Rolling cache retention window.")
    start.add_argument("--segment-seconds", type=int, default=int(ARCHIVE_DEFAULTS["segment_seconds"]), help="Audio segment length.")
    start.add_argument("--permanent", action="store_true", default=bool(ARCHIVE_DEFAULTS["permanent"]), help="Keep all segments instead of pruning the cache.")
    start.add_argument("--transcribe", action="store_true", default=bool(ARCHIVE_DEFAULTS["transcribe"]), help="Run timestamped transcription alongside recording.")
    start.add_argument(
        "--transcribe-backend",
        choices=("auto", "vosk", "openai", "nemo"),
        default=str(ARCHIVE_DEFAULTS["transcribe_backend"]),
        help="Transcription backend for sidecar transcripts.",
    )
    start.add_argument(
        "--transcribe-model",
        default=str(ARCHIVE_DEFAULTS["transcribe_model"]),
        help="Transcription model for transcript sidecars. OpenAI and NeMo backends interpret this differently.",
    )
    start.add_argument("--transcribe-prompt", default=str(ARCHIVE_DEFAULTS["transcribe_prompt"]), help="Optional prompt/vocabulary hint for transcription.")
    start.add_argument("--sample-rate", type=int, default=int(ARCHIVE_DEFAULTS["sample_rate"]), help="Recorded/transcribed audio sample rate.")
    start.add_argument("--metadata-poll-seconds", type=float, default=float(ARCHIVE_DEFAULTS["metadata_poll_seconds"]), help="How often to poll gqrx metadata.")
    start.add_argument("--lang", default=str(ARCHIVE_DEFAULTS["lang"]), help="Language hint for transcription.")

    stop = sub.add_parser("stop", help="Stop a named archive job.")
    stop.add_argument("name")

    status = sub.add_parser("status", help="Show archive job status.")
    status.add_argument("name", nargs="?")

    log = sub.add_parser("log", help="Show recent archive events.")
    log.add_argument("name")
    log.add_argument("--lines", type=int, default=40)

    run = sub.add_parser("_run", help=argparse.SUPPRESS)
    run.add_argument("--job-dir", required=True)

    return parser.parse_args()


def job_dir_for(name: str) -> Path:
    return JOBS_DIR / name


def load_status(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "status.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def start_job(args: argparse.Namespace) -> int:
    source = args.source or get_default_monitor_source()
    if not source:
        raise SystemExit("could not determine PulseAudio monitor source")

    job_dir = job_dir_for(args.name)
    pid_path = job_dir / "pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = 0
        if pid and is_pid_running(pid):
            raise SystemExit(f"archive job '{args.name}' is already running (pid {pid})")

    config = ArchiveConfig(
        name=args.name,
        source=source,
        cache_hours=args.cache_hours,
        segment_seconds=args.segment_seconds,
        permanent=args.permanent,
        transcribe=args.transcribe,
        transcribe_backend=args.transcribe_backend,
        transcribe_model=args.transcribe_model,
        transcribe_prompt=args.transcribe_prompt,
        sample_rate=args.sample_rate,
        metadata_poll_seconds=args.metadata_poll_seconds,
        language=args.lang,
    )
    job_dir.mkdir(parents=True, exist_ok=True)
    config_path = job_dir / "config.json"
    config_path.write_text(json.dumps(asdict(config), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    stdout_log = (job_dir / "launcher.log").open("a", encoding="utf-8")
    cmd = [sys.executable, str(Path(__file__)), "_run", "--job-dir", str(job_dir)]
    proc = subprocess.Popen(cmd, stdout=stdout_log, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(1)
    if proc.poll() is not None:
        raise SystemExit(f"archive job '{args.name}' exited immediately; check {job_dir / 'launcher.log'}")
    print(f"archive job '{args.name}' started (pid {proc.pid})")
    print(f"job_dir: {job_dir}")
    print(f"audio_dir: {job_dir / 'audio'}")
    print(f"events: {job_dir / 'events.jsonl'}")
    print(f"transcripts: {job_dir / 'transcripts.jsonl'}")
    return 0


def stop_job(args: argparse.Namespace) -> int:
    job_dir = job_dir_for(args.name)
    pid_path = job_dir / "pid"
    if not pid_path.exists():
        raise SystemExit(f"archive job '{args.name}' is not running")
    pid = int(pid_path.read_text(encoding="utf-8").strip())
    if not is_pid_running(pid):
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
        raise SystemExit(f"archive job '{args.name}' is not running")
    os.kill(pid, signal.SIGTERM)
    print(f"stopping archive job '{args.name}' (pid {pid})")
    return 0


def status_job(args: argparse.Namespace) -> int:
    if args.name:
        jobs = [job_dir_for(args.name)]
    else:
        jobs = sorted(path for path in JOBS_DIR.iterdir() if path.is_dir()) if JOBS_DIR.exists() else []

    if not jobs:
        print("no archive jobs found")
        return 0

    for job_dir in jobs:
        status = load_status(job_dir)
        pid_path = job_dir / "pid"
        pid = None
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
            except ValueError:
                pid = None
        active = bool(pid and is_pid_running(pid))
        print(f"name: {job_dir.name}")
        print(f"  active: {'yes' if active else 'no'}")
        print(f"  pid: {pid if pid else 'n/a'}")
        print(f"  source: {status.get('source', 'n/a')}")
        print(f"  audio_dir: {status.get('audio_dir', str(job_dir / 'audio'))}")
        print(f"  segments: {status.get('segments', 'n/a')}")
        print(f"  last_segment: {status.get('last_segment', 'n/a')}")
        print(f"  transcribe: {status.get('transcribe', 'n/a')}")
        print(f"  backend: {status.get('transcribe_backend', 'n/a')}")
        print(f"  last_transcript_at: {status.get('last_transcript_at', 'n/a')}")
        print(f"  last_transcript_text: {status.get('last_transcript_text', 'n/a')}")
    return 0


def log_job(args: argparse.Namespace) -> int:
    path = job_dir_for(args.name) / "events.jsonl"
    if not path.exists():
        raise SystemExit(f"no events log for archive job '{args.name}'")
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines[-args.lines :]:
        print(line)
    return 0


def run_job(args: argparse.Namespace) -> int:
    job_dir = Path(args.job_dir)
    config = ArchiveConfig(**json.loads((job_dir / "config.json").read_text(encoding="utf-8")))
    return ArchiveWorker(config, job_dir).run()


def main() -> int:
    args = parse_args()
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    if args.cmd == "start":
        return start_job(args)
    if args.cmd == "stop":
        return stop_job(args)
    if args.cmd == "status":
        return status_job(args)
    if args.cmd == "log":
        return log_job(args)
    if args.cmd == "_run":
        return run_job(args)
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
