#!/home/david/.venvs/radio/bin/python
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import osmosdr
from gnuradio import analog, audio, blocks, filter, gr
from gnuradio.fft import window
from gnuradio.filter import firdes
from radio_config import expand_path, load_radio_config

CONFIG = load_radio_config()
PATHS_CONFIG = CONFIG["paths"]
MULTI_DEFAULTS = CONFIG["multichannel"]
BASE_DIR = Path(expand_path(PATHS_CONFIG["state_dir"])) / "multichannel"
JOBS_DIR = BASE_DIR / "jobs"
TRANSCRIBE_SCRIPT = Path(__file__).with_name("radio_transcribe.py")
SESSION_SCRIPT = Path(__file__).with_name("radio_session.py")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@dataclass
class ChannelConfig:
    name: str
    frequency_mhz: float
    mode: str
    play: bool = False
    volume: float = 1.0


@dataclass
class JobConfig:
    name: str
    center_mhz: float
    rf_rate: int
    segment_seconds: int
    cache_hours: float
    permanent: bool
    sample_rate: int
    audio_device: str
    transcribe: bool
    transcribe_backend: str
    transcribe_model: str
    transcribe_prompt: str
    language: str
    channels: list[ChannelConfig]


def parse_channel_spec(spec: str) -> ChannelConfig:
    parts = [part.strip() for part in spec.split(":")]
    if len(parts) < 2:
        raise argparse.ArgumentTypeError("channel spec must be name:freq[:mode[:play]]")
    name = parts[0]
    try:
        frequency_mhz = float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid channel frequency in '{spec}'") from exc
    mode = parts[2].lower() if len(parts) >= 3 and parts[2] else "nfm"
    play = any(part.lower() == "play" for part in parts[3:]) or (len(parts) >= 4 and parts[3].lower() == "play")
    if mode not in {"nfm", "wfm", "am"}:
        raise argparse.ArgumentTypeError(f"unsupported channel mode '{mode}' in '{spec}'")
    return ChannelConfig(name=name, frequency_mhz=frequency_mhz, mode=mode, play=play)


def write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")


def session_command(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(SESSION_SCRIPT), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


class MultiChannelFlowgraph(gr.top_block):
    def __init__(
        self,
        *,
        center_mhz: float,
        rf_rate: int,
        sample_rate: int,
        channels: list[ChannelConfig],
        record_fifos: dict[str, str],
        transcribe_fifos: dict[str, str],
        audio_device: str,
    ) -> None:
        super().__init__("hackrf-multichannel")

        center_hz = center_mhz * 1_000_000
        source = osmosdr.source(args="numchan=1 hackrf=0")
        source.set_sample_rate(rf_rate)
        source.set_center_freq(center_hz)
        source.set_freq_corr(0)
        source.set_dc_offset_mode(1)
        source.set_iq_balance_mode(0)
        source.set_gain_mode(False)
        source.set_gain(0, "AMP")
        source.set_gain(32, "LNA")
        source.set_gain(40, "VGA")
        source.set_bandwidth(min(rf_rate, 1_750_000))
        self.source = source

        self.blocks_to_hold: list[Any] = [source]
        if 250_000 % sample_rate != 0:
            raise SystemExit(f"sample_rate {sample_rate} must evenly divide 250000 for NFM/AM channels")
        if any(channel.mode == "wfm" for channel in channels) and sample_rate != 50_000:
            raise SystemExit("WFM multichannel mode currently requires --sample-rate 50000")

        playback_seen = False
        for channel in channels:
            offset_hz = int(round((channel.frequency_mhz - center_mhz) * 1_000_000))
            if abs(offset_hz) > (rf_rate // 2) - 150_000:
                raise SystemExit(
                    f"channel {channel.name} at {channel.frequency_mhz} MHz is outside usable span around center {center_mhz} MHz"
                )
            audio_stream = self._build_channel(source, channel, offset_hz, rf_rate, sample_rate)

            volume = blocks.multiply_const_ff(channel.volume)
            short_record = blocks.float_to_short(1, 32767)
            short_transcribe = blocks.float_to_short(1, 32767)
            record_sink = blocks.file_sink(gr.sizeof_short, record_fifos[channel.name], False)
            transcribe_sink = blocks.file_sink(gr.sizeof_short, transcribe_fifos[channel.name], False)
            record_sink.set_unbuffered(True)
            transcribe_sink.set_unbuffered(True)
            self.connect(audio_stream, volume)
            self.connect(volume, short_record, record_sink)
            self.connect(volume, short_transcribe, transcribe_sink)
            self.blocks_to_hold.extend([volume, short_record, short_transcribe, record_sink, transcribe_sink])

            if channel.play and not playback_seen:
                sink = audio.sink(sample_rate, audio_device, True)
                self.connect(volume, sink)
                self.blocks_to_hold.append(sink)
                playback_seen = True

    def _build_channel(
        self,
        source: osmosdr.source,
        channel: ChannelConfig,
        offset_hz: int,
        rf_rate: int,
        sample_rate: int,
    ):
        if channel.mode == "wfm":
            quad_rate = 250_000
            decimation = rf_rate // quad_rate
            taps = firdes.low_pass(1.0, rf_rate, 100_000, 50_000, window.WIN_HAMMING)
            xlating = filter.freq_xlating_fir_filter_ccf(decimation, taps, offset_hz, rf_rate)
            demod = analog.wfm_rcv(quad_rate=quad_rate, audio_decimation=5)
            self.connect(source, xlating, demod)
            self.blocks_to_hold.extend([xlating, demod])
            return demod

        quad_rate = 250_000
        decimation = rf_rate // quad_rate
        if channel.mode == "nfm":
            taps = firdes.low_pass(1.0, rf_rate, 8_000, 4_000, window.WIN_HAMMING)
            xlating = filter.freq_xlating_fir_filter_ccf(decimation, taps, offset_hz, rf_rate)
            demod = analog.nbfm_rx(audio_rate=sample_rate, quad_rate=quad_rate, tau=75e-6, max_dev=5_000)
            self.connect(source, xlating, demod)
            self.blocks_to_hold.extend([xlating, demod])
            return demod

        taps = firdes.low_pass(1.0, rf_rate, 6_000, 2_500, window.WIN_HAMMING)
        xlating = filter.freq_xlating_fir_filter_ccf(decimation, taps, offset_hz, rf_rate)
        demod = analog.am_demod_cf(channel_rate=quad_rate, audio_decim=5, audio_pass=5_000, audio_stop=6_000)
        self.connect(source, xlating, demod)
        self.blocks_to_hold.extend([xlating, demod])
        return demod


class MultiChannelWorker:
    def __init__(self, config: JobConfig, job_dir: Path, session_token: str) -> None:
        self.config = config
        self.job_dir = job_dir
        self.session_token = session_token
        self.events_path = self.job_dir / "events.jsonl"
        self.status_path = self.job_dir / "status.json"
        self.pid_path = self.job_dir / "pid"
        self._stop = threading.Event()
        self._procs: list[subprocess.Popen[str]] = []
        self._flowgraph: Optional[MultiChannelFlowgraph] = None
        self._status: dict[str, Any] = {
            "name": config.name,
            "started_at": now_iso(),
            "center_mhz": config.center_mhz,
            "rf_rate": config.rf_rate,
            "channels": [asdict(ch) for ch in config.channels],
            "segments": {ch.name: 0 for ch in config.channels},
            "last_segment": {ch.name: None for ch in config.channels},
            "last_transcript": {ch.name: None for ch in config.channels},
        }

    def channel_dir(self, channel: ChannelConfig) -> Path:
        return self.job_dir / "channels" / channel.name

    def write_event(self, event: str, **fields: Any) -> None:
        write_jsonl(self.events_path, {"ts": now_iso(), "event": event, **fields})

    def write_status(self) -> None:
        self.status_path.write_text(json.dumps(self._status, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def ensure_fifos(self, channel: ChannelConfig) -> tuple[str, str]:
        ch_dir = self.channel_dir(channel)
        ch_dir.mkdir(parents=True, exist_ok=True)
        record_fifo = ch_dir / "record.pcm"
        transcribe_fifo = ch_dir / "transcribe.pcm"
        for fifo in (record_fifo, transcribe_fifo):
            if fifo.exists():
                fifo.unlink()
            os.mkfifo(fifo)
        return str(record_fifo), str(transcribe_fifo)

    def start_ffmpeg(self, channel: ChannelConfig, fifo_path: str) -> subprocess.Popen[str]:
        audio_dir = self.channel_dir(channel) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        pattern = str(audio_dir / "%Y%m%d-%H%M%S.flac")
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-f",
            "s16le",
            "-ar",
            str(self.config.sample_rate),
            "-ac",
            "1",
            "-i",
            fifo_path,
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
        self.write_event("ffmpeg_start", channel=channel.name, cmd=cmd)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)

        def read_ffmpeg() -> None:
            assert proc.stderr is not None
            for raw in proc.stderr:
                line = raw.strip()
                if not line:
                    continue
                self.write_event("ffmpeg_log", channel=channel.name, line=line)
                if "Opening '" in line and ".flac' for writing" in line:
                    segment = line.split("Opening '", 1)[1].split("' for writing", 1)[0]
                    self.write_event("segment_open", channel=channel.name, path=segment)

        threading.Thread(target=read_ffmpeg, daemon=True).start()
        return proc

    def start_transcriber(self, channel: ChannelConfig, fifo_path: str) -> Optional[subprocess.Popen[str]]:
        if not self.config.transcribe:
            return None
        ch_dir = self.channel_dir(channel)
        transcripts_path = ch_dir / "transcripts.jsonl"
        cmd = [
            sys.executable,
            str(TRANSCRIBE_SCRIPT),
            "--backend",
            self.config.transcribe_backend,
            "--lang",
            self.config.language,
            "--sample-rate",
            str(self.config.sample_rate),
            "--input-file",
            fifo_path,
            "--json",
        ]
        if self.config.transcribe_backend == "openai":
            cmd.extend(["--openai-model", self.config.transcribe_model])
        elif self.config.transcribe_backend == "nemo":
            cmd.extend(["--nemo-model", self.config.transcribe_model])
        if self.config.transcribe_prompt:
            cmd.extend(["--prompt", self.config.transcribe_prompt])
        self.write_event("transcriber_start", channel=channel.name, cmd=cmd)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        def read_transcripts() -> None:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    self.write_event("transcriber_log", channel=channel.name, line=line)
                    continue
                record = {
                    "ts": now_iso(),
                    "channel": channel.name,
                    "backend": self.config.transcribe_backend,
                    "model": self.config.transcribe_model,
                    **payload,
                }
                write_jsonl(transcripts_path, record)
                if payload.get("event") == "final" and payload.get("text"):
                    self._status["last_transcript"][channel.name] = payload["text"]
                    self.write_status()

        threading.Thread(target=read_transcripts, daemon=True).start()
        return proc

    def enforce_retention(self) -> None:
        if self.config.permanent:
            return
        cutoff = time.time() - (self.config.cache_hours * 3600)
        for channel in self.config.channels:
            for path in sorted((self.channel_dir(channel) / "audio").glob("*.flac")):
                try:
                    if path.stat().st_mtime >= cutoff:
                        continue
                except FileNotFoundError:
                    continue
                try:
                    path.unlink()
                    self.write_event("segment_deleted", channel=channel.name, path=str(path), reason="cache_expired")
                except FileNotFoundError:
                    pass

    def refresh_status(self) -> None:
        for channel in self.config.channels:
            audio_dir = self.channel_dir(channel) / "audio"
            segments = sorted(audio_dir.glob("*.flac"))
            self._status["segments"][channel.name] = len(segments)
            self._status["last_segment"][channel.name] = str(segments[-1]) if segments else None
        self.write_status()

    def run(self) -> int:
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        session_command(
            "activate-primary",
            "--token",
            self.session_token,
            "--pid",
            str(os.getpid()),
            "--detail",
            f"center_mhz={self.config.center_mhz}",
            "--detail",
            f"rf_rate={self.config.rf_rate}",
            "--detail",
            f"job_dir={self.job_dir}",
        )
        (self.job_dir / "config.json").write_text(
            json.dumps(asdict(self.config), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.write_event("session_start", **asdict(self.config))
        self.write_status()

        record_fifos: dict[str, str] = {}
        transcribe_fifos: dict[str, str] = {}
        for channel in self.config.channels:
            record_fifo, transcribe_fifo = self.ensure_fifos(channel)
            record_fifos[channel.name] = record_fifo
            transcribe_fifos[channel.name] = transcribe_fifo

        for channel in self.config.channels:
            self._procs.append(self.start_ffmpeg(channel, record_fifos[channel.name]))
            transcribe_proc = self.start_transcriber(channel, transcribe_fifos[channel.name])
            if transcribe_proc is not None:
                self._procs.append(transcribe_proc)

        self._flowgraph = MultiChannelFlowgraph(
            center_mhz=self.config.center_mhz,
            rf_rate=self.config.rf_rate,
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
            record_fifos=record_fifos,
            transcribe_fifos=transcribe_fifos,
            audio_device=self.config.audio_device,
        )

        def stop(_signum: int, _frame: object) -> None:
            self._stop.set()

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

        self._flowgraph.start()
        try:
            last_refresh = 0.0
            last_retention = 0.0
            while not self._stop.is_set():
                now = time.time()
                if now - last_refresh >= 5:
                    self.refresh_status()
                    last_refresh = now
                if now - last_retention >= 30:
                    self.enforce_retention()
                    last_retention = now
                time.sleep(0.5)
        finally:
            if self._flowgraph is not None:
                self._flowgraph.stop()
                self._flowgraph.wait()
            for proc in self._procs:
                if proc.poll() is None:
                    proc.terminate()
            for proc in self._procs:
                if proc.poll() is None:
                    try:
                        proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            self.refresh_status()
            self._status["stopped_at"] = now_iso()
            self.write_event("session_stop")
            self.write_status()
            try:
                session_command("release", "--pid", str(os.getpid()))
            except subprocess.CalledProcessError:
                pass
            try:
                self.pid_path.unlink()
            except FileNotFoundError:
                pass
            for channel in self.config.channels:
                for fifo_name in ("record.pcm", "transcribe.pcm"):
                    fifo = self.channel_dir(channel) / fifo_name
                    try:
                        fifo.unlink()
                    except FileNotFoundError:
                        pass
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simultaneous nearby-station record/playback from one HackRF center frequency.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="Start a named multichannel job.")
    start.add_argument("name")
    start.add_argument("--center", type=float, required=True, help="Center frequency in MHz.")
    start.add_argument("--channel", action="append", required=True, type=parse_channel_spec, help="Channel spec: name:freq[:mode[:play]]")
    start.add_argument("--rf-rate", type=int, default=int(MULTI_DEFAULTS["rf_rate"]), help="HackRF sample rate.")
    start.add_argument("--sample-rate", type=int, default=int(MULTI_DEFAULTS["sample_rate"]), help="Per-channel audio sample rate.")
    start.add_argument("--segment-seconds", type=int, default=int(MULTI_DEFAULTS["segment_seconds"]), help="Segment length per channel.")
    start.add_argument("--cache-hours", type=float, default=float(MULTI_DEFAULTS["cache_hours"]), help="Rolling retention window.")
    start.add_argument("--permanent", action="store_true", default=bool(MULTI_DEFAULTS["permanent"]), help="Keep segments permanently.")
    start.add_argument("--audio-device", default=str(MULTI_DEFAULTS["audio_device"]), help="Optional GNU Radio audio device for the playback channel.")
    start.add_argument("--transcribe", action="store_true", default=bool(MULTI_DEFAULTS["transcribe"]), help="Transcribe each channel.")
    start.add_argument("--transcribe-backend", choices=("auto", "vosk", "openai", "nemo"), default=str(MULTI_DEFAULTS["transcribe_backend"]))
    start.add_argument("--transcribe-model", default=str(MULTI_DEFAULTS["transcribe_model"]))
    start.add_argument("--transcribe-prompt", default=str(MULTI_DEFAULTS["transcribe_prompt"]))
    start.add_argument("--lang", default=str(MULTI_DEFAULTS["lang"]))

    stop = sub.add_parser("stop", help="Stop a running multichannel job.")
    stop.add_argument("name")

    status = sub.add_parser("status", help="Show multichannel job status.")
    status.add_argument("name", nargs="?")

    log = sub.add_parser("log", help="Show recent job events.")
    log.add_argument("name")
    log.add_argument("--lines", type=int, default=40)

    run = sub.add_parser("_run", help=argparse.SUPPRESS)
    run.add_argument("--job-dir", required=True)
    run.add_argument("--session-token", required=True)
    return parser.parse_args()


def job_dir_for(name: str) -> Path:
    return JOBS_DIR / name


def load_status(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "status.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def start_job(args: argparse.Namespace) -> int:
    job_dir = job_dir_for(args.name)
    pid_path = job_dir / "pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = 0
        if pid and is_pid_running(pid):
            raise SystemExit(f"multichannel job '{args.name}' is already running (pid {pid})")

    channels: list[ChannelConfig] = args.channel
    if sum(1 for ch in channels if ch.play) > 1:
        raise SystemExit("only one channel may be tagged with :play")

    config = JobConfig(
        name=args.name,
        center_mhz=args.center,
        rf_rate=args.rf_rate,
        segment_seconds=args.segment_seconds,
        cache_hours=args.cache_hours,
        permanent=args.permanent,
        sample_rate=args.sample_rate,
        audio_device=args.audio_device,
        transcribe=args.transcribe,
        transcribe_backend=args.transcribe_backend,
        transcribe_model=args.transcribe_model,
        transcribe_prompt=args.transcribe_prompt,
        language=args.lang,
        channels=channels,
    )
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "config.json").write_text(json.dumps(asdict(config), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    launcher_log = (job_dir / "launcher.log").open("a", encoding="utf-8")
    try:
        session_token = session_command(
            "reserve-primary",
            "--backend",
            "multichannel",
            "--owner",
            args.name,
            "--pid",
            str(os.getpid()),
            "--detail",
            f"center_mhz={args.center}",
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() if exc.stderr else "radio primary is busy"
        raise SystemExit(message)
    cmd = [sys.executable, str(Path(__file__)), "_run", "--job-dir", str(job_dir), "--session-token", session_token]
    proc = subprocess.Popen(cmd, stdout=launcher_log, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(1)
    if proc.poll() is not None:
        try:
            session_command("release", "--token", session_token)
        except subprocess.CalledProcessError:
            pass
        raise SystemExit(f"multichannel job '{args.name}' exited immediately; check {job_dir / 'launcher.log'}")
    print(f"multichannel job '{args.name}' started (pid {proc.pid})")
    print(f"job_dir: {job_dir}")
    return 0


def stop_job(args: argparse.Namespace) -> int:
    pid_path = job_dir_for(args.name) / "pid"
    if not pid_path.exists():
        raise SystemExit(f"multichannel job '{args.name}' is not running")
    pid = int(pid_path.read_text(encoding="utf-8").strip())
    os.kill(pid, signal.SIGTERM)
    print(f"stopping multichannel job '{args.name}' (pid {pid})")
    return 0


def status_job(args: argparse.Namespace) -> int:
    jobs = [job_dir_for(args.name)] if args.name else sorted(JOBS_DIR.iterdir()) if JOBS_DIR.exists() else []
    if not jobs:
        print("no multichannel jobs found")
        return 0
    for job_dir in jobs:
        if not job_dir.is_dir():
            continue
        status = load_status(job_dir)
        pid_path = job_dir / "pid"
        pid = int(pid_path.read_text(encoding="utf-8").strip()) if pid_path.exists() else None
        print(f"name: {job_dir.name}")
        print(f"  active: {'yes' if pid and is_pid_running(pid) else 'no'}")
        print(f"  center_mhz: {status.get('center_mhz', 'n/a')}")
        print(f"  channels: {', '.join(ch['name'] for ch in status.get('channels', []))}")
        print(f"  segments: {status.get('segments', {})}")
        print(f"  last_transcript: {status.get('last_transcript', {})}")
    return 0


def log_job(args: argparse.Namespace) -> int:
    path = job_dir_for(args.name) / "events.jsonl"
    if not path.exists():
        raise SystemExit(f"no events log for multichannel job '{args.name}'")
    for line in path.read_text(encoding="utf-8").splitlines()[-args.lines :]:
        print(line)
    return 0


def run_job(args: argparse.Namespace) -> int:
    job_dir = Path(args.job_dir)
    raw = json.loads((job_dir / "config.json").read_text(encoding="utf-8"))
    raw["channels"] = [ChannelConfig(**channel) for channel in raw["channels"]]
    config = JobConfig(**raw)
    return MultiChannelWorker(config, job_dir, args.session_token).run()


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
