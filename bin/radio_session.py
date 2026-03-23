#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from radio_config import expand_path, load_radio_config

CONFIG = load_radio_config()
PATHS_CONFIG = CONFIG["paths"]
SESSION_DEFAULTS = CONFIG["session"]
BASE_DIR = Path(expand_path(PATHS_CONFIG["state_dir"]))
SESSION_PATH = BASE_DIR / "session.json"
LOCK_PATH = BASE_DIR / "session.lock"
DEGRADED_TIMEOUT_SECONDS = int(SESSION_DEFAULTS["degraded_timeout_seconds"])


def now_ts() -> float:
    return time.time()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def remote_command(host: str, port: int, command: str, timeout: float = 1.0) -> str | None:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall((command + "\n").encode("utf-8"))
            data = sock.recv(4096)
    except OSError:
        return None
    if not data:
        return None
    return data.decode(errors="replace").strip()


def empty_session() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": now_iso(),
        "active_backend": None,
        "primary": None,
        "aux": [],
        "history": [],
    }


def parse_detail_pairs(pairs: list[str]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"detail must be key=value, got '{pair}'")
        key, value = pair.split("=", 1)
        details[key] = value
    return details


def load_session() -> dict[str, Any]:
    if not SESSION_PATH.exists():
        return empty_session()
    try:
        raw = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_session()
    session = empty_session()
    session.update(raw)
    session.setdefault("aux", [])
    session.setdefault("history", [])
    return session


def trim_history(session: dict[str, Any]) -> None:
    session["history"] = session.get("history", [])[-30:]


def append_history(session: dict[str, Any], event: str, **fields: Any) -> None:
    session.setdefault("history", []).append({"ts": now_iso(), "event": event, **fields})
    trim_history(session)


def primary_health(primary: dict[str, Any]) -> str:
    pid = int(primary.get("pid", 0) or 0)
    if not is_pid_running(pid):
        return "dead"
    if primary.get("backend") != "gqrx":
        return "alive"
    details = primary.get("details", {})
    host = details.get("remote_host", "127.0.0.1")
    try:
        port = int(details.get("remote_port", 7356))
    except (TypeError, ValueError):
        port = 7356
    if remote_command(host, port, "_") is None:
        if not primary.get("degraded_since"):
            primary["degraded_since"] = now_iso()
            primary["degraded_since_ts"] = now_ts()
        degraded_since_ts = float(primary.get("degraded_since_ts", 0) or 0)
        if degraded_since_ts and (now_ts() - degraded_since_ts) >= DEGRADED_TIMEOUT_SECONDS:
            return "dead"
        return "degraded"
    primary.pop("degraded_since", None)
    primary.pop("degraded_since_ts", None)
    return "healthy"


def prune_session(session: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    primary = session.get("primary")
    if primary:
        state = primary.get("state")
        pid = int(primary.get("pid", 0) or 0)
        if state == "reserved":
            expires_at = float(primary.get("expires_at", 0) or 0)
            if expires_at and expires_at < now_ts():
                notes.append(f"expired reservation for {primary.get('backend')}")
                append_history(session, "reservation_expired", backend=primary.get("backend"), owner=primary.get("owner"))
                session["primary"] = None
        else:
            health = primary_health(primary)
            primary["health"] = health
            if health == "dead":
                notes.append(f"cleared stale primary {primary.get('backend')}")
                append_history(session, "primary_cleared", backend=primary.get("backend"), owner=primary.get("owner"))
                session["primary"] = None

    live_aux: list[dict[str, Any]] = []
    for item in session.get("aux", []):
        pid = int(item.get("pid", 0) or 0)
        if is_pid_running(pid):
            live_aux.append(item)
            continue
        notes.append(f"cleared stale aux {item.get('backend')}:{item.get('owner')}")
        append_history(session, "aux_cleared", backend=item.get("backend"), owner=item.get("owner"))
    session["aux"] = live_aux

    primary = session.get("primary")
    session["active_backend"] = primary.get("backend") if primary and primary.get("state") == "active" else None
    session["updated_at"] = now_iso()
    return notes


def save_session(session: dict[str, Any]) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    session["updated_at"] = now_iso()
    SESSION_PATH.write_text(json.dumps(session, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class SessionLock:
    def __enter__(self) -> "SessionLock":
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.handle = LOCK_PATH.open("a+", encoding="utf-8")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def describe_primary(primary: dict[str, Any] | None) -> str:
    if not primary:
        return "idle"
    backend = primary.get("backend", "unknown")
    owner = primary.get("owner", "unknown")
    state = primary.get("state", "unknown")
    pid = primary.get("pid", "n/a")
    health = primary.get("health")
    bits = [f"{backend}:{owner}", f"state={state}", f"pid={pid}"]
    if health:
        bits.append(f"health={health}")
    return ", ".join(bits)


def cmd_reserve_primary(args: argparse.Namespace) -> int:
    details = parse_detail_pairs(args.detail)
    with SessionLock():
        session = load_session()
        prune_session(session)
        primary = session.get("primary")
        if primary:
            raise SystemExit(f"radio primary is busy: {describe_primary(primary)}")
        token = uuid.uuid4().hex
        session["primary"] = {
            "backend": args.backend,
            "owner": args.owner,
            "pid": args.pid,
            "reserved_at": now_iso(),
            "expires_at": now_ts() + args.ttl,
            "state": "reserved",
            "token": token,
            "details": details,
        }
        append_history(session, "primary_reserved", backend=args.backend, owner=args.owner, pid=args.pid)
        prune_session(session)
        save_session(session)
    print(token)
    return 0


def cmd_activate_primary(args: argparse.Namespace) -> int:
    details = parse_detail_pairs(args.detail)
    with SessionLock():
        session = load_session()
        prune_session(session)
        primary = session.get("primary")
        if not primary or primary.get("token") != args.token:
            raise SystemExit("session reservation token is missing or expired")
        primary["state"] = "active"
        primary["pid"] = args.pid
        primary["started_at"] = now_iso()
        primary["expires_at"] = None
        primary["health"] = "alive"
        if details:
            merged = dict(primary.get("details", {}))
            merged.update(details)
            primary["details"] = merged
        append_history(session, "primary_activated", backend=primary.get("backend"), owner=primary.get("owner"), pid=args.pid)
        prune_session(session)
        save_session(session)
    return 0


def cmd_register_aux(args: argparse.Namespace) -> int:
    details = parse_detail_pairs(args.detail)
    with SessionLock():
        session = load_session()
        prune_session(session)
        aux = [item for item in session.get("aux", []) if int(item.get("pid", 0) or 0) != args.pid]
        aux.append(
            {
                "backend": args.backend,
                "owner": args.owner,
                "pid": args.pid,
                "started_at": now_iso(),
                "details": details,
            }
        )
        session["aux"] = aux
        append_history(session, "aux_registered", backend=args.backend, owner=args.owner, pid=args.pid)
        save_session(session)
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    with SessionLock():
        session = load_session()
        prune_session(session)
        removed = False
        primary = session.get("primary")
        if primary and ((args.token and primary.get("token") == args.token) or (args.pid and int(primary.get("pid", 0) or 0) == args.pid)):
            append_history(session, "primary_released", backend=primary.get("backend"), owner=primary.get("owner"), pid=primary.get("pid"))
            session["primary"] = None
            removed = True
        new_aux = []
        for item in session.get("aux", []):
            pid = int(item.get("pid", 0) or 0)
            if (args.pid and pid == args.pid) or (args.owner and item.get("owner") == args.owner and item.get("backend") == args.backend):
                append_history(session, "aux_released", backend=item.get("backend"), owner=item.get("owner"), pid=item.get("pid"))
                removed = True
                continue
            new_aux.append(item)
        session["aux"] = new_aux
        prune_session(session)
        save_session(session)
    return 0 if removed else 1


def cmd_status(args: argparse.Namespace) -> int:
    with SessionLock():
        session = load_session()
        notes = prune_session(session)
        save_session(session)
    if args.json:
        print(json.dumps(session, indent=2, sort_keys=True))
        return 0
    print(f"active_backend: {session.get('active_backend') or 'idle'}")
    print(f"primary: {describe_primary(session.get('primary'))}")
    if notes:
        print("notes:")
        for note in notes:
            print(f"  - {note}")
    aux = session.get("aux", [])
    print(f"aux_count: {len(aux)}")
    for item in aux:
        print(f"  aux: {item.get('backend')}:{item.get('owner')} pid={item.get('pid')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shared session state for the radio stack.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    reserve = sub.add_parser("reserve-primary", help="Reserve the shared SDR primary backend.")
    reserve.add_argument("--backend", required=True)
    reserve.add_argument("--owner", required=True)
    reserve.add_argument("--pid", required=True, type=int)
    reserve.add_argument("--ttl", type=int, default=45)
    reserve.add_argument("--detail", action="append", default=[])
    reserve.set_defaults(func=cmd_reserve_primary)

    activate = sub.add_parser("activate-primary", help="Promote a reservation to the active primary backend.")
    activate.add_argument("--token", required=True)
    activate.add_argument("--pid", required=True, type=int)
    activate.add_argument("--detail", action="append", default=[])
    activate.set_defaults(func=cmd_activate_primary)

    aux = sub.add_parser("register-aux", help="Register a non-owning helper job.")
    aux.add_argument("--backend", required=True)
    aux.add_argument("--owner", required=True)
    aux.add_argument("--pid", required=True, type=int)
    aux.add_argument("--detail", action="append", default=[])
    aux.set_defaults(func=cmd_register_aux)

    release = sub.add_parser("release", help="Release a primary or auxiliary registration.")
    release.add_argument("--token")
    release.add_argument("--pid", type=int)
    release.add_argument("--backend")
    release.add_argument("--owner")
    release.set_defaults(func=cmd_release)

    status = sub.add_parser("status", help="Show the current session state.")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
