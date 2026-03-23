#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import shelve
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import discord


ROOT_DIR = Path("/home/david/random")
LOG_DIR = ROOT_DIR / "logs"
DEFAULT_SERVICE = "masterbot.service"
DEFAULT_DB_PATH = LOG_DIR / "masterbot.db"
DEFAULT_LOG_FILES = [LOG_DIR / "masterbot.log", LOG_DIR / "masterbot.out"]
DEFAULT_EVENTS_PATH = LOG_DIR / "voice_events_recent.json"
DEFAULT_FAILURES_PATH = LOG_DIR / "voice_failures.json"

RE_CODE = re.compile(r"ConnectionClosed.*code[=\s](\d+)")
RE_ENDPOINT = re.compile(r"Voice handshake complete.*Endpoint found:\s*([^\s]+)")
RE_RETRY = re.compile(
    r"VOICE connect retry guild=(\d+) channel=(\d+) attempt=(\d+)/(\d+) delay_s=([0-9.]+)"
)
RE_FAIL_FINAL = re.compile(r"voice connect failed guild=(\d+) channel=(\d+) after (\d+) attempts", re.I)
RE_VOICE_STATE = re.compile(r"VOICE_STATE self guild=(\d+) before=(\d+) after=(\d+)")
RE_CHANNEL_TERM = re.compile(r"terminated for Channel ID (\d+) \(Guild ID (\d+)\)")
RE_HANDSHAKE_ATTEMPT = re.compile(r"Starting voice handshake\.\.\. \(connection attempt (\d+)\)")
RE_SHARD = re.compile(r"Shard ID ([^ ]+) has connected to Gateway")
RE_UDP_DISCOVERY = re.compile(r"udp|ip discovery", re.I)
RE_SECRET_KEY = re.compile(r"secret key|secret_key|xsalsa20", re.I)
RE_LOCAL_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)?\s+")


@dataclass
class Line:
    ts: datetime
    source: str
    msg: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts_iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_local_line_ts(line: str, now: datetime) -> Optional[datetime]:
    m = RE_LOCAL_TS.search(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _read_journal_lines(service: str, since_minutes: int) -> List[Line]:
    cmd = [
        "journalctl",
        "--user",
        "-u",
        service,
        "--since",
        f"{max(1, since_minutes)} minutes ago",
        "--no-pager",
        "-o",
        "json",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return []

    lines: List[Line] = []
    for raw in out.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        msg = str(obj.get("MESSAGE", "") or "")
        rt = str(obj.get("__REALTIME_TIMESTAMP", "") or "")
        ts: Optional[datetime] = None
        if rt.isdigit():
            ts = datetime.fromtimestamp(int(rt) / 1_000_000.0, tz=timezone.utc)
        if ts is None:
            continue
        lines.append(Line(ts=ts, source="journal", msg=msg))
    return lines


def _read_local_logs(log_files: List[Path], window_start: datetime, window_end: datetime) -> List[Line]:
    lines: List[Line] = []
    now = _utcnow()
    for path in log_files:
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for raw in content:
            ts = _parse_local_line_ts(raw, now)
            if ts is None:
                continue
            if ts < window_start or ts > window_end:
                continue
            lines.append(Line(ts=ts, source=str(path), msg=raw))
    return lines


def _load_runtime_config(db_path: Path) -> Dict[str, Any]:
    if not db_path.exists():
        return {}
    try:
        with shelve.open(str(db_path)) as db:
            cfg = db.get("runtime_config")
            return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _load_recent_events(events_path: Path, window_start: datetime, window_end: datetime) -> List[Dict[str, Any]]:
    if not events_path.exists():
        return []
    try:
        obj = json.loads(events_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    events = obj.get("events", []) if isinstance(obj, dict) else []
    out: List[Dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ts = float(ev.get("ts_epoch", 0.0) or 0.0)
        if ts <= 0:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if window_start <= dt <= window_end:
            row = dict(ev)
            row["ts_iso"] = _ts_iso(dt)
            out.append(row)
    return out


def _load_failure_rollup(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _parse_lines(lines: List[Line]) -> Dict[str, Any]:
    parsed_events: List[Dict[str, Any]] = []
    close_codes: Counter[int] = Counter()
    retry_delays: List[float] = []
    shard_ids: set[str] = set()
    endpoints_seen: List[Tuple[datetime, str]] = []
    endpoint_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"attempts": 0, "failures": 0})
    voice_state_flap_ts: List[datetime] = []

    last_gid: Optional[int] = None
    last_cid: Optional[int] = None
    last_attempt: Optional[int] = None

    for ln in sorted(lines, key=lambda x: x.ts):
        msg = ln.msg
        m_shard = RE_SHARD.search(msg)
        if m_shard:
            shard_ids.add(m_shard.group(1))

        m_term = RE_CHANNEL_TERM.search(msg)
        if m_term:
            last_cid = int(m_term.group(1))
            last_gid = int(m_term.group(2))

        m_hs_attempt = RE_HANDSHAKE_ATTEMPT.search(msg)
        if m_hs_attempt:
            last_attempt = int(m_hs_attempt.group(1))

        m_ep = RE_ENDPOINT.search(msg)
        if m_ep:
            endpoint = m_ep.group(1).strip()
            endpoints_seen.append((ln.ts, endpoint))
            endpoint_stats[endpoint]["attempts"] += 1
            parsed_events.append(
                {
                    "ts": _ts_iso(ln.ts),
                    "type": "handshake_complete",
                    "endpoint": endpoint,
                    "guild_id": last_gid,
                    "channel_id": last_cid,
                    "attempt": last_attempt,
                    "source": ln.source,
                }
            )

        m_retry = RE_RETRY.search(msg)
        if m_retry:
            gid, cid, attempt, _total, delay = m_retry.groups()
            delay_f = float(delay)
            retry_delays.append(delay_f)
            parsed_events.append(
                {
                    "ts": _ts_iso(ln.ts),
                    "type": "connect_retry",
                    "guild_id": int(gid),
                    "channel_id": int(cid),
                    "attempt": int(attempt),
                    "delay_s": delay_f,
                    "source": ln.source,
                }
            )

        m_vs = RE_VOICE_STATE.search(msg)
        if m_vs:
            gid, before, after = m_vs.groups()
            b = int(before)
            a = int(after)
            if (b == 0 and a != 0) or (b != 0 and a == 0):
                voice_state_flap_ts.append(ln.ts)
            parsed_events.append(
                {
                    "ts": _ts_iso(ln.ts),
                    "type": "voice_state",
                    "guild_id": int(gid),
                    "before": b,
                    "after": a,
                    "source": ln.source,
                }
            )

        m_final = RE_FAIL_FINAL.search(msg)
        if m_final:
            gid, cid, attempts = m_final.groups()
            parsed_events.append(
                {
                    "ts": _ts_iso(ln.ts),
                    "type": "connect_failed",
                    "guild_id": int(gid),
                    "channel_id": int(cid),
                    "attempt": int(attempts),
                    "source": ln.source,
                }
            )

        m_code = RE_CODE.search(msg)
        if m_code:
            code = int(m_code.group(1))
            close_codes[code] += 1
            endpoint = None
            for ts_ep, ep in reversed(endpoints_seen):
                if (ln.ts - ts_ep).total_seconds() <= 60:
                    endpoint = ep
                    break
            if endpoint:
                endpoint_stats[endpoint]["failures"] += 1
            parsed_events.append(
                {
                    "ts": _ts_iso(ln.ts),
                    "type": "connection_closed",
                    "code": code,
                    "guild_id": last_gid,
                    "channel_id": last_cid,
                    "endpoint": endpoint,
                    "attempt": last_attempt,
                    "source": ln.source,
                }
            )

    flap_ts_sorted = sorted(voice_state_flap_ts)
    flap_intervals = [
        (flap_ts_sorted[i] - flap_ts_sorted[i - 1]).total_seconds()
        for i in range(1, len(flap_ts_sorted))
    ]
    avg_flap_interval_s = statistics.mean(flap_intervals) if flap_intervals else None

    longest_streak = 0
    current_streak = 0
    prev_ts: Optional[datetime] = None
    for ts in flap_ts_sorted:
        if prev_ts is None or (ts - prev_ts).total_seconds() <= 120:
            current_streak += 1
        else:
            current_streak = 1
        longest_streak = max(longest_streak, current_streak)
        prev_ts = ts

    retry_pattern = {
        "delays_s": retry_delays,
        "exponential_backoff_engaged": False,
    }
    if len(retry_delays) >= 3:
        monotonic = all(retry_delays[i] >= retry_delays[i - 1] for i in range(1, len(retry_delays)))
        retry_pattern["exponential_backoff_engaged"] = monotonic and len(set(retry_delays)) >= 3

    udp_discovery_lines = []
    secret_key_lines = []
    for ln in lines:
        if RE_UDP_DISCOVERY.search(ln.msg):
            udp_discovery_lines.append({"ts": _ts_iso(ln.ts), "msg": ln.msg})
        if RE_SECRET_KEY.search(ln.msg):
            secret_key_lines.append({"ts": _ts_iso(ln.ts), "msg": ln.msg})

    return {
        "events": parsed_events,
        "close_codes": dict(sorted(close_codes.items())),
        "retry_pattern": retry_pattern,
        "endpoint_distribution": dict(endpoint_stats),
        "flaps": {
            "total_flaps": len(flap_ts_sorted),
            "longest_continuous_flap_streak": longest_streak,
            "avg_time_between_flaps_s": avg_flap_interval_s,
            "flap_duration_s": (
                (flap_ts_sorted[-1] - flap_ts_sorted[0]).total_seconds() if len(flap_ts_sorted) >= 2 else 0.0
            ),
            "timestamps": [_ts_iso(x) for x in flap_ts_sorted],
        },
        "shard_ids_observed": sorted(shard_ids),
        "udp_discovery": udp_discovery_lines,
        "secret_key_exchange": secret_key_lines,
    }


def _runtime_context(
    cfg: Dict[str, Any],
    recent_events: List[Dict[str, Any]],
    parsed: Dict[str, Any],
    first_failure_ts: Optional[str],
) -> Dict[str, Any]:
    concurrent_clients = None
    target_epoch: Optional[float] = None
    if first_failure_ts:
        with_timestamp = first_failure_ts.replace("Z", "+00:00")
        with_timezone = datetime.fromisoformat(with_timestamp)
        target_epoch = with_timezone.timestamp()
    if target_epoch is not None:
        candidates = [
            ev
            for ev in recent_events
            if isinstance(ev, dict) and float(ev.get("ts_epoch", 0.0) or 0.0) <= target_epoch
        ]
        for ev in reversed(candidates):
            val = ev.get("active_voice_clients")
            if isinstance(val, int):
                concurrent_clients = val
                break
    if concurrent_clients is None:
        for ev in reversed(recent_events):
            val = ev.get("active_voice_clients")
            if isinstance(val, int):
                concurrent_clients = val
                break

    shard_ids = parsed.get("shard_ids_observed", [])
    shard_count = None
    if shard_ids:
        clean = [x for x in shard_ids if x.lower() != "none"]
        shard_count = len(clean) if clean else 1

    return {
        "first_failure_ts": first_failure_ts,
        "discord_py_version": getattr(discord, "__version__", "unknown"),
        "python_version": platform.python_version(),
        "os_version": platform.platform(),
        "active_voice_channels_configured": cfg.get("voice_loop_preset_channel_ids", []),
        "concurrent_voice_clients": concurrent_clients,
        "shard_count": shard_count,
    }


def _summary_text(payload: Dict[str, Any]) -> str:
    window = payload["window"]
    parsed = payload["parsed"]
    events = parsed["events"]
    failures = [e for e in events if e.get("type") == "connection_closed"]
    attempts = parsed["retry_pattern"]["delays_s"]
    close_codes = parsed["close_codes"]
    endpoint_counts = parsed["endpoint_distribution"]
    flaps = parsed["flaps"]

    lines = [
        "VOICE INCIDENT REPORT",
        "---------------------",
        f"Window: {window['start_iso']} to {window['end_iso']} UTC",
        f"Total Attempts: {len(attempts)}",
        f"Failures: {len(failures)}",
        "Close Codes:",
    ]
    if close_codes:
        for code, n in close_codes.items():
            lines.append(f"  {code}: {n}")
    else:
        lines.append("  <none>")
    lines.append("Endpoints:")
    if endpoint_counts:
        for endpoint, stats in endpoint_counts.items():
            lines.append(f"  {endpoint}: attempts={stats.get('attempts', 0)} failures={stats.get('failures', 0)}")
    else:
        lines.append("  <none>")
    avg = flaps.get("avg_time_between_flaps_s")
    avg_txt = f"{avg:.2f}s" if isinstance(avg, (int, float)) else "n/a"
    flap_duration_s = float(flaps.get("flap_duration_s", 0.0) or 0.0)
    flap_duration_txt = str(timedelta(seconds=int(flap_duration_s)))
    lines.append(
        f"Flaps: total={flaps.get('total_flaps', 0)} "
        f"longest_streak={flaps.get('longest_continuous_flap_streak', 0)} avg_delta={avg_txt}"
    )
    lines.append(f"Flap Duration: {flap_duration_txt}")
    lines.append(
        f"Backoff: {'engaged' if parsed['retry_pattern'].get('exponential_backoff_engaged') else 'not-detected'} "
        f"delays={parsed['retry_pattern'].get('delays_s', [])}"
    )
    ctx = payload.get("runtime_context", {})
    resolution = payload.get("resolution_hint", "undetermined")
    lines.append(
        "Runtime: "
        f"discord.py={ctx.get('discord_py_version')} python={ctx.get('python_version')} "
        f"shards={ctx.get('shard_count')} clients={ctx.get('concurrent_voice_clients')}"
    )
    lines.append(f"Resolution: {resolution}")
    return "\n".join(lines)


async def _post_to_discord(channel_id: int, token: str, summary: str, report_path: Path) -> None:
    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            if not hasattr(channel, "send"):
                raise RuntimeError(f"Channel {channel_id} is not messageable")
            await channel.send(
                f"voice incident report generated ({report_path.name})\n```text\n{summary[:1400]}\n```",
                file=discord.File(str(report_path)),
            )
        finally:
            await client.close()

    await client.start(token)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate structured voice incident report.")
    parser.add_argument("--since", type=int, default=30, help="Window in minutes (default: 30)")
    parser.add_argument("--service", default=DEFAULT_SERVICE)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--failures-path", default=str(DEFAULT_FAILURES_PATH))
    parser.add_argument("--post-to", type=int, default=0, help="Optional Discord text channel ID")
    parser.add_argument("--token", default=os.getenv("DISCORD_TOKEN", ""))
    args = parser.parse_args()

    end_ts = _utcnow()
    start_ts = end_ts - timedelta(minutes=max(1, args.since))

    journal_lines = _read_journal_lines(args.service, args.since)
    local_lines = _read_local_logs(DEFAULT_LOG_FILES, start_ts, end_ts)
    all_lines = sorted(journal_lines + local_lines, key=lambda x: x.ts)
    parsed = _parse_lines(all_lines)

    recent_events = _load_recent_events(Path(args.events_path), start_ts, end_ts)
    failure_rollup = _load_failure_rollup(Path(args.failures_path))
    cfg = _load_runtime_config(Path(args.db_path))
    failure_events = [e for e in parsed["events"] if e.get("type") == "connection_closed"]
    first_failure_ts = failure_events[0]["ts"] if failure_events else None

    payload = {
        "generated_at": _ts_iso(end_ts),
        "window": {
            "since_minutes": max(1, args.since),
            "start_iso": _ts_iso(start_ts),
            "end_iso": _ts_iso(end_ts),
        },
        "sources": {
            "service": args.service,
            "journal_lines": len(journal_lines),
            "local_log_lines": len(local_lines),
            "events_path": str(args.events_path),
            "failures_path": str(args.failures_path),
        },
        "parsed": parsed,
        "recent_events": recent_events,
        "voice_failures_rollup": failure_rollup,
        "runtime_context": _runtime_context(cfg, recent_events, parsed, first_failure_ts),
    }

    script_mtime = (ROOT_DIR / "bin" / "masterbot_v3.py").stat().st_mtime if (ROOT_DIR / "bin" / "masterbot_v3.py").exists() else 0.0
    payload["resolution_hint"] = (
        "possible code/runtime changes during window"
        if (script_mtime and script_mtime >= start_ts.timestamp())
        else "spontaneous (no code changes detected in window)"
    )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = end_ts.strftime("%Y%m%d_%H%M%S")
    out_path = LOG_DIR / f"voice_incident_{stamp}.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    summary = _summary_text(payload)
    print(summary)
    print(f"JSON_PATH: {out_path}")

    if args.post_to:
        if not args.token.strip():
            print("WARN: --post-to used but DISCORD_TOKEN is missing; skipping upload.", file=sys.stderr)
            return 0
        asyncio.run(_post_to_discord(args.post_to, args.token.strip(), summary, out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
