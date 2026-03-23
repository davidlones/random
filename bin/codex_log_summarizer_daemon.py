#!/usr/bin/env python3
"""Background summarizer for Codex session logs using local LLM."""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import shlex
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SESSIONS_ROOT = Path("/home/david/.codex/sessions")
LOCAL_AI_CHAT = Path("/home/david/random/bin/local_ai_chat.py")
DEFAULT_MD_OUT = Path("/home/david/random/logs/codex_context_summary.md")
DEFAULT_JSON_OUT = Path("/home/david/random/logs/codex_context_summary.json")
LOG = logging.getLogger("codex_log_summary")

WRITE_MARKERS = [
    "cat >",
    "cat >>",
    "zip ",
    "mkdir -p",
    "rm -f",
    "mv ",
    "cp ",
    "touch ",
    "git add ",
    "write_text(",
    "writestr(",
    "os.replace(",
    "shelve.open(",
    "save-stream",
]

PATCH_FILE_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")
RANDOM_PATH_RE = re.compile(r"/home/david/random(?:/[^\s\"']+)?")


def _extract_random_paths(cmd: str) -> list[str]:
    out: set[str] = set()
    try:
        tokens = shlex.split(cmd, posix=True)
    except Exception:
        tokens = []
    for tok in tokens:
        if tok.startswith("/home/david/random"):
            out.add(tok.rstrip("\"'"))
    if not out:
        for p in RANDOM_PATH_RE.findall(cmd):
            if p.startswith("/home/david/random"):
                out.add(p.rstrip("\"'"))
    return sorted(out)


def _path_looks_real(path: str) -> bool:
    if any(x in path for x in ("$", "|", "\\", "*", "?", "{", "}")):
        return False
    return Path(path).exists()


@dataclass
class ParsedData:
    generated_at_utc: str
    window_start_utc: str
    window_label: str
    all_entry_count: int
    top_level_type_counts: dict[str, int]
    response_item_type_counts: dict[str, int]
    function_call_name_counts: dict[str, int]
    event_msg_type_counts: dict[str, int]
    recent_global_events: list[dict[str, str]]
    user_messages: list[dict[str, str]]
    file_events: list[dict[str, Any]]


class LocalLLM:
    def __init__(self, start_if_needed: bool) -> None:
        self.start_if_needed = start_if_needed
        self.mod = None
        self.server_proc = None
        self.base_url = "http://127.0.0.1:18080"

    def _load_mod(self) -> None:
        if self.mod is not None:
            return
        if not LOCAL_AI_CHAT.exists():
            raise RuntimeError(f"local_ai_chat.py not found: {LOCAL_AI_CHAT}")
        spec = importlib.util.spec_from_file_location("local_ai_chat_mod", str(LOCAL_AI_CHAT))
        if spec is None or spec.loader is None:
            raise RuntimeError("failed to load local_ai_chat module")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.mod = mod
        LOG.info("llm.module_loaded path=%s", LOCAL_AI_CHAT)

    def _ensure_server(self) -> None:
        self._load_mod()
        assert self.mod is not None
        try:
            LOG.info("llm.server_check base_url=%s", self.base_url)
            self.mod.wait_for_server(self.base_url, timeout_s=3)
            LOG.info("llm.server_ready base_url=%s source=existing", self.base_url)
            return
        except Exception:
            if not self.start_if_needed:
                raise RuntimeError("local llama-server is not reachable and auto-start is disabled")
            LOG.info("llm.server_not_ready base_url=%s action=start", self.base_url)

        args = SimpleNamespace(
            server_bin=self.mod.DEFAULT_SERVER_BIN,
            model=self.mod.DEFAULT_MODEL,
            host="127.0.0.1",
            port=18080,
            ctx_size=4096,
            gpu_layers="all",
            keep_server=True,
            verbose_server=False,
        )
        self.server_proc = self.mod.start_llama_server(args)
        self.mod.wait_for_server(self.base_url, timeout_s=90)
        LOG.info("llm.server_ready base_url=%s source=started pid=%s", self.base_url, getattr(self.server_proc, "pid", "?"))

    def summarize(self, prompt: str) -> str:
        self._ensure_server()
        assert self.mod is not None
        model_id = self.mod.get_model_id(self.base_url, fallback=os.path.basename(self.mod.DEFAULT_MODEL))
        LOG.info("llm.summarize_start model=%s prompt_chars=%s", model_id, len(prompt))
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a concise ops summarizer. Return markdown bullets only. "
                    "Do not invent files or actions. Use only the provided data. "
                    "If data is missing, say unknown."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        text = self.mod.chat_once(
            base_url=self.base_url,
            model_id=model_id,
            messages=messages,
            temperature=0.2,
            top_p=0.9,
            max_tokens=350,
            timeout=180,
        )
        LOG.info("llm.summarize_done model=%s response_chars=%s", model_id, len(text.strip()))
        return text.strip()


def _iter_session_files() -> list[Path]:
    if not SESSIONS_ROOT.exists():
        return []
    return sorted(SESSIONS_ROOT.rglob("rollout-*.jsonl"))


def _safe_json_load(s: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(s)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def parse_logs(window_hours: int, all_history: bool = False) -> ParsedData:
    now = datetime.now(timezone.utc)
    cutoff = None if all_history else (now - timedelta(hours=window_hours))

    all_entry_count = 0
    top_level_type_counts: Counter[str] = Counter()
    response_item_type_counts: Counter[str] = Counter()
    function_call_name_counts: Counter[str] = Counter()
    event_msg_type_counts: Counter[str] = Counter()
    recent_global_events: list[dict[str, str]] = []

    user_messages: list[dict[str, str]] = []
    file_events: list[dict[str, Any]] = []

    for f in _iter_session_files():
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                obj = _safe_json_load(line)
                if not obj:
                    continue
                ts_raw = obj.get("timestamp")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except Exception:
                    continue
                if cutoff is not None and ts < cutoff:
                    continue
                all_entry_count += 1

                typ = obj.get("type")
                if isinstance(typ, str):
                    top_level_type_counts[typ] += 1
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                p_subtype = ""
                if typ == "response_item":
                    p_subtype = str(payload.get("type") or "")
                    if p_subtype:
                        response_item_type_counts[p_subtype] += 1
                    if p_subtype == "function_call":
                        fn_name = str(payload.get("name") or "")
                        if fn_name:
                            function_call_name_counts[fn_name] += 1
                elif typ == "event_msg":
                    p_subtype = str(payload.get("type") or "")
                    if p_subtype:
                        event_msg_type_counts[p_subtype] += 1

                # Keep a rolling compact view of recent events across all types.
                summary = ""
                if typ == "event_msg":
                    if payload.get("type") == "user_message":
                        summary = str(payload.get("message") or "")[:220]
                    elif payload.get("type") == "agent_message":
                        summary = str(payload.get("message") or "")[:220]
                    else:
                        summary = str(payload.get("type") or "")[:220]
                elif typ == "response_item":
                    summary = f"{payload.get('type') or ''}:{payload.get('name') or ''}"[:220]
                else:
                    summary = str(typ or "")[:220]
                recent_global_events.append(
                    {
                        "ts": ts.isoformat(),
                        "type": str(typ or ""),
                        "subtype": p_subtype,
                        "summary": summary,
                    }
                )
                if len(recent_global_events) > 500:
                    recent_global_events = recent_global_events[-500:]

                if typ == "event_msg" and payload.get("type") == "user_message":
                    msg = str(payload.get("message") or "").strip()
                    if msg:
                        user_messages.append({"ts": ts.isoformat(), "message": msg})
                    continue

                if typ != "response_item":
                    continue

                ptype = payload.get("type")
                if ptype == "custom_tool_call" and payload.get("name") == "apply_patch":
                    patch = str(payload.get("input") or "")
                    for pl in patch.splitlines():
                        m = PATCH_FILE_RE.match(pl)
                        if not m:
                            continue
                        action, path = m.group(1).lower(), m.group(2).strip()
                        if not path.startswith("/home/david/random"):
                            continue
                        rel = path.replace("/home/david/random/", "") if path != "/home/david/random" else "."
                        file_events.append(
                            {
                                "ts": ts.isoformat(),
                                "source": "apply_patch",
                                "action": action,
                                "path": path,
                                "rel": rel,
                                "evidence": "apply_patch",
                            }
                        )
                    continue

                if ptype == "function_call" and payload.get("name") == "exec_command":
                    args_raw = payload.get("arguments")
                    if not isinstance(args_raw, str):
                        continue
                    args = _safe_json_load(args_raw)
                    if not args:
                        continue
                    cmd = str(args.get("cmd") or "")
                    low = cmd.lower()
                    if not any(m in low for m in WRITE_MARKERS):
                        continue
                    for path in _extract_random_paths(cmd):
                        if not path.startswith("/home/david/random"):
                            continue
                        if path == "/home/david/random":
                            continue
                        if not _path_looks_real(path):
                            continue
                        rel = path.replace("/home/david/random/", "") if path != "/home/david/random" else "."
                        file_events.append(
                            {
                                "ts": ts.isoformat(),
                                "source": "exec_command",
                                "action": "write_like_cmd",
                                "path": path,
                                "rel": rel,
                                "evidence": cmd[:240].replace("\n", " "),
                            }
                        )

    return ParsedData(
        generated_at_utc=now.isoformat(),
        window_start_utc=(cutoff.isoformat() if cutoff is not None else "all-history"),
        window_label=("all-history" if cutoff is None else f"last-{window_hours}h"),
        all_entry_count=all_entry_count,
        top_level_type_counts=dict(top_level_type_counts),
        response_item_type_counts=dict(response_item_type_counts),
        function_call_name_counts=dict(function_call_name_counts),
        event_msg_type_counts=dict(event_msg_type_counts),
        recent_global_events=recent_global_events[-120:],
        user_messages=user_messages,
        file_events=file_events,
    )


def aggregate(parsed: ParsedData) -> dict[str, Any]:
    by_file: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "first_ts": None,
            "last_ts": None,
            "events": 0,
            "actions": set(),
            "sources": set(),
            "sample_evidence": None,
        }
    )

    for ev in parsed.file_events:
        p = ev["path"]
        row = by_file[p]
        ts = ev["ts"]
        if row["first_ts"] is None or ts < row["first_ts"]:
            row["first_ts"] = ts
        if row["last_ts"] is None or ts > row["last_ts"]:
            row["last_ts"] = ts
        row["events"] += 1
        row["actions"].add(ev["action"])
        row["sources"].add(ev["source"])
        if row["sample_evidence"] is None:
            row["sample_evidence"] = ev["evidence"]

    files = []
    for path, row in by_file.items():
        files.append(
            {
                "path": path,
                "rel": path.replace("/home/david/random/", "") if path != "/home/david/random" else ".",
                "first_ts": row["first_ts"],
                "last_ts": row["last_ts"],
                "events": row["events"],
                "actions": sorted(row["actions"]),
                "sources": sorted(row["sources"]),
                "sample_evidence": row["sample_evidence"],
            }
        )

    files.sort(key=lambda x: (x["events"], x["last_ts"] or ""), reverse=True)

    user_msgs = parsed.user_messages
    top_user_terms = Counter()
    for m in user_msgs:
        txt = m["message"].lower()
        for tok in re.findall(r"[a-z][a-z0-9_\-]{2,}", txt):
            if tok in {"the", "and", "for", "with", "this", "that", "from", "your", "have", "just", "into"}:
                continue
            top_user_terms[tok] += 1

    return {
        "generated_at_utc": parsed.generated_at_utc,
        "window_start_utc": parsed.window_start_utc,
        "window_label": parsed.window_label,
        "all_entry_count": parsed.all_entry_count,
        "top_level_type_counts": dict(sorted(parsed.top_level_type_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "response_item_type_counts": dict(
            sorted(parsed.response_item_type_counts.items(), key=lambda kv: kv[1], reverse=True)
        ),
        "function_call_name_counts": dict(
            sorted(parsed.function_call_name_counts.items(), key=lambda kv: kv[1], reverse=True)
        ),
        "event_msg_type_counts": dict(sorted(parsed.event_msg_type_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "user_message_count": len(user_msgs),
        "file_event_count": len(parsed.file_events),
        "unique_files": len(files),
        "top_user_terms": top_user_terms.most_common(25),
        "recent_user_messages": user_msgs[-20:],
        "recent_global_events": parsed.recent_global_events[-80:],
        "files": files,
    }


def build_llm_prompt(data: dict[str, Any]) -> str:
    top_files = data["files"][:20]
    lines = [
        "Summarize these Codex log changes as concise operational context for future coding sessions.",
        "Output 8-12 markdown bullets. Use ONLY the listed files/actions; do not infer deletes/removals.",
        "Include one risk bullet based on uncertainty/noise in logs, not fabricated behavior.",
        f"window_label={data.get('window_label', 'unknown')}",
        f"window_start_utc={data['window_start_utc']}",
        f"generated_at_utc={data['generated_at_utc']}",
        f"user_messages={data['user_message_count']}",
        f"all_entries={data.get('all_entry_count', 0)}",
        f"file_events={data['file_event_count']}",
        f"unique_files={data['unique_files']}",
        f"top_level_types={data.get('top_level_type_counts', {})}",
        f"response_item_types={data.get('response_item_type_counts', {})}",
        f"event_msg_types={data.get('event_msg_type_counts', {})}",
        f"function_calls={data.get('function_call_name_counts', {})}",
        "Top files:",
    ]
    for f in top_files:
        lines.append(
            f"- {f['rel']} | events={f['events']} | actions={','.join(f['actions'])} | sources={','.join(f['sources'])}"
        )
    return "\n".join(lines)


def build_markdown(data: dict[str, Any], llm_summary: str | None) -> str:
    lines: list[str] = []
    lines.append("# Codex Context Summary (Rolling Window)")
    lines.append("")
    lines.append(f"- Generated (UTC): {data['generated_at_utc']}")
    lines.append(f"- Window label: {data.get('window_label', 'unknown')}")
    lines.append(f"- Window start (UTC): {data['window_start_utc']}")
    lines.append(f"- All log entries in window: {data.get('all_entry_count', 0)}")
    lines.append(f"- User messages: {data['user_message_count']}")
    lines.append(f"- File events: {data['file_event_count']}")
    lines.append(f"- Unique files: {data['unique_files']}")
    lines.append("")

    lines.append("## Event Coverage")
    lines.append("")
    lines.append("- Summarization scope: all session log entries in the time window.")
    lines.append(f"- Coverage: 100% of `{data.get('all_entry_count', 0)}` entries were parsed and counted.")
    lines.append("")

    lines.append("## Event Type Breakdown")
    lines.append("")
    for k, v in data.get("top_level_type_counts", {}).items():
        lines.append(f"- top_level `{k}`: {v}")
    for k, v in list(data.get("response_item_type_counts", {}).items())[:12]:
        lines.append(f"- response_item `{k}`: {v}")
    for k, v in list(data.get("event_msg_type_counts", {}).items())[:12]:
        lines.append(f"- event_msg `{k}`: {v}")
    for k, v in list(data.get("function_call_name_counts", {}).items())[:12]:
        lines.append(f"- function_call `{k}`: {v}")
    lines.append("")

    lines.append("## Top File Changes")
    lines.append("")
    for f in data["files"][:25]:
        lines.append(f"- `{f['rel']}` | events={f['events']} | actions={','.join(f['actions'])} | sources={','.join(f['sources'])}")

    lines.append("")
    lines.append("## Top User Terms")
    lines.append("")
    for term, count in data["top_user_terms"][:20]:
        lines.append(f"- `{term}`: {count}")

    lines.append("")
    lines.append("## Recent User Messages")
    lines.append("")
    for m in data["recent_user_messages"][-12:]:
        lines.append(f"- {m['ts']}: {m['message']}")

    lines.append("")
    lines.append("## Recent Global Events")
    lines.append("")
    for ev in data.get("recent_global_events", [])[-20:]:
        lines.append(f"- {ev.get('ts')}: {ev.get('type')}/{ev.get('subtype')} {ev.get('summary')}")

    if llm_summary:
        lines.append("")
        lines.append("## Local LLM Summary")
        lines.append("")
        lines.append(llm_summary)

    return "\n".join(lines) + "\n"


def llm_summary_is_trustworthy(text: str) -> bool:
    low = text.lower()
    forbidden = [
        "removed",
        "delete",
        "deleted",
        "corruption",
        "vulnerab",
        "security risk",
    ]
    if any(tok in low for tok in forbidden):
        return False
    if low.count("the log includes updates to") > 2:
        return False
    if len(text) > 1800:
        return False
    return True


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def run_cycle(args: argparse.Namespace, llm: LocalLLM | None) -> None:
    cycle_start = datetime.now(timezone.utc)
    LOG.info(
        "cycle.start window_hours=%s all_history=%s interval=%s use_llm=%s",
        args.window_hours,
        bool(args.all_history),
        args.interval,
        bool(llm is not None),
    )
    parsed = parse_logs(args.window_hours, all_history=args.all_history)
    data = aggregate(parsed)
    LOG.info(
        "cycle.parsed user_messages=%s file_events=%s unique_files=%s",
        data["user_message_count"],
        data["file_event_count"],
        data["unique_files"],
    )

    llm_summary = None
    if llm is not None:
        try:
            top_files = [f["rel"] for f in data.get("files", [])[:5]]
            LOG.info("llm.context_top_files files=%s", ",".join(top_files))
            candidate = llm.summarize(build_llm_prompt(data))
            if llm_summary_is_trustworthy(candidate):
                llm_summary = candidate
                LOG.info("llm.summary_accept chars=%s", len(candidate))
            else:
                llm_summary = "- Local LLM output suppressed due to low-confidence/hallucination signals."
                LOG.warning("llm.summary_suppressed reason=trust_filter chars=%s", len(candidate))
        except Exception as exc:
            llm_summary = f"- Local LLM summary unavailable: {exc}"
            LOG.exception("llm.summary_error err=%s", exc)

    md = build_markdown(data, llm_summary)
    atomic_write(args.out_md, md)
    atomic_write(args.out_json, json.dumps(data, indent=2))
    elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    LOG.info(
        "cycle.done out_md=%s out_json=%s duration_s=%.2f",
        args.out_md,
        args.out_json,
        elapsed,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daemonize Codex log summarization")
    p.add_argument("--window-hours", type=int, default=48)
    p.add_argument("--all-history", action="store_true", help="Process all available session history")
    p.add_argument("--interval", type=int, default=900, help="Seconds between summary refreshes")
    p.add_argument("--out-md", type=Path, default=DEFAULT_MD_OUT)
    p.add_argument("--out-json", type=Path, default=DEFAULT_JSON_OUT)
    p.add_argument("--use-llm", action="store_true", help="Use local LLM for executive summary")
    p.add_argument("--start-server-if-needed", action="store_true", help="Start local llama-server if not running")
    p.add_argument("--once", action="store_true", help="Run one cycle and exit")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s codex-log-summary %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    llm = LocalLLM(start_if_needed=args.start_server_if_needed) if args.use_llm else None

    while True:
        run_cycle(args, llm)
        sleep_s = max(30, args.interval)
        next_run = datetime.now(timezone.utc) + timedelta(seconds=sleep_s)
        LOG.info("cycle.sleep seconds=%s next_run_utc=%s", sleep_s, next_run.isoformat())
        if args.once:
            return 0
        time.sleep(sleep_s)


if __name__ == "__main__":
    raise SystemExit(main())
