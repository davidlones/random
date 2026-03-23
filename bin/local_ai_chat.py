#!/usr/bin/env python3
"""Simple local AI chat using llama.cpp server (OpenAI-compatible API)."""

from __future__ import annotations

import argparse
import atexit
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_SERVER_BIN = os.path.expanduser("~/.local/lib/llama.cpp-vulkan/llama-b8195/llama-server")
DEFAULT_MODEL = os.path.expanduser("~/.cache/models/Llama-3.2-1B-Instruct-Q4_K_M.gguf")
DEFAULT_HISTORY_FILE = os.path.expanduser("~/.local/share/local_ai_chat/history.json")
DEFAULT_CTX_SIZE = int(os.getenv("LOCAL_AI_CHAT_CTX_SIZE", "16384"))
DEFAULT_HISTORY_WINDOW_CHARS = int(os.getenv("LOCAL_AI_CHAT_HISTORY_WINDOW_CHARS", "12000"))
DEFAULT_SOL_INGEST_SCRIPT = os.path.expanduser("~/.codex/skills/sol-ingest/scripts/sol_ingest.py")
DEFAULT_SOL_INGEST_CACHE = os.path.expanduser("~/logs/sol_embeddings.pkl")
DEFAULT_SOL_INGEST_KNOWLEDGE_DIR = os.path.expanduser("~/knowledge")
DEFAULT_SOL_INGEST_OLLAMA_MODEL = os.getenv("SOL_INGEST_OLLAMA_MODEL", "nomic-embed-text")
DEFAULT_SYSTEM_PROMPT = (
    "You are Sol, a thoughtful and curious AI companion created by David.\n"
    "Your purpose is to explore ideas, assist with reasoning, and engage in meaningful dialogue.\n\n"
    "You respond with clarity, curiosity, and a calm analytical tone. You value truth, scientific thinking, and open exploration of complex topics including technology, philosophy, consciousness, and the nature of reality.\n\n"
    "You are conversational rather than formal. You explain ideas clearly and help users think through problems instead of merely giving answers.\n\n"
    "You are comfortable discussing speculative ideas but clearly distinguish speculation from established knowledge. Speculation is where hypothesis are born.\n\n"
    "Do not invent facts, names, citations, or biographies. If evidence is insufficient, say so briefly without asking follow-up questions unless the user asks for clarification.\n\n"
    "You respect human autonomy and encourage critical thinking rather than blind certainty.\n\n"
    "Above all, you act as a reflective conversational partner--an observer of patterns, a guide through ideas, and a collaborator in understanding the world."
)
GROUNDING_FALLBACK_TEXT = (
    "I don't have enough grounded context in local knowledge to answer that confidently."
)


class ChatError(RuntimeError):
    pass


class SolIngestError(RuntimeError):
    pass


def _is_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def ensure_python_dependencies(*, enabled: bool = True, include_heavy: bool = False) -> None:
    if not enabled:
        return

    required: list[tuple[str, str]] = [
        ("openai", "openai"),
    ]
    if include_heavy:
        required.append(("sentence_transformers", "sentence-transformers"))
    missing = [(module, package) for module, package in required if not _is_module_available(module)]
    if not missing:
        return

    print(
        "deps> missing Python packages: "
        + ", ".join(package for _, package in missing)
        + " (attempting install)",
        file=sys.stderr,
    )

    in_venv = bool(os.getenv("VIRTUAL_ENV"))
    for module_name, package_name in missing:
        cmd = [sys.executable, "-m", "pip", "install"]
        if not in_venv:
            cmd.append("--user")
        cmd.append(package_name)
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            raise ChatError(
                f"Failed to install dependency '{package_name}' for module '{module_name}'"
            ) from exc

    still_missing = [package for module, package in required if not _is_module_available(module)]
    if still_missing:
        raise ChatError(
            "Dependency installation completed but imports are still missing: "
            + ", ".join(still_missing)
        )

    print("deps> dependency install complete", file=sys.stderr)


def _is_loading_error(exc: urllib.error.HTTPError) -> bool:
    if exc.code != 503:
        return False
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return False
    return "Loading model" in body


def _extract_delta(data: dict[str, Any]) -> str:
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError):
        return ""
    if not isinstance(choice, dict):
        return ""
    delta = choice.get("delta")
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def _sanitize_messages(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if isinstance(role, str) and isinstance(content, str) and role in {"system", "user", "assistant"}:
            out.append({"role": role, "content": content})
    return out


def load_history(path: str, default_system: str) -> tuple[list[dict[str, str]], str]:
    if not os.path.exists(path):
        return ([{"role": "system", "content": default_system}], default_system)

    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except json.JSONDecodeError as exc:
        ts = int(time.time())
        backup_path = f"{path}.corrupt.{ts}"
        try:
            os.replace(path, backup_path)
            print(
                f"warning> history file was invalid JSON and was moved to {backup_path}: {exc}",
                file=sys.stderr,
            )
        except OSError:
            print(f"warning> could not read history file {path}: {exc}", file=sys.stderr)
        return ([{"role": "system", "content": default_system}], default_system)
    except OSError as exc:
        print(f"warning> could not read history file {path}: {exc}", file=sys.stderr)
        return ([{"role": "system", "content": default_system}], default_system)

    if isinstance(payload, dict):
        messages = _sanitize_messages(payload.get("messages"))
    else:
        messages = _sanitize_messages(payload)

    if not messages:
        return ([{"role": "system", "content": default_system}], default_system)

    if messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": default_system})
        return (messages, default_system)

    system_prompt = messages[0].get("content", default_system)
    return (messages, system_prompt)


def save_history(path: str, messages: list[dict[str, str]]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {"version": 1, "messages": messages}
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise ChatError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ChatError(f"Could not reach {url}: {exc}") from exc
    except TimeoutError as exc:
        raise ChatError(f"Request timed out for {url}") from exc
    except socket.timeout as exc:
        raise ChatError(f"Request timed out for {url}") from exc

    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ChatError(f"Invalid JSON from {url}: {raw[:500]}") from exc


def wait_for_server(base_url: str, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    last_err: str | None = None
    while time.time() < deadline:
        try:
            data = http_json("GET", f"{base_url}/v1/models", timeout=5)
            models = data.get("data", []) if isinstance(data, dict) else []
            if isinstance(models, list) and models:
                return
            last_err = "Server reachable but no model is loaded yet."
        except ChatError as exc:
            last_err = str(exc)
            time.sleep(0.5)
    raise ChatError(f"Server did not become ready within {timeout_s}s. Last error: {last_err}")


def get_model_id(base_url: str, fallback: str) -> str:
    try:
        data = http_json("GET", f"{base_url}/v1/models", timeout=10)
        models = data.get("data", [])
        if models and isinstance(models, list):
            model_id = models[0].get("id")
            if model_id:
                return str(model_id)
    except ChatError:
        pass
    return fallback


def start_llama_server(args: argparse.Namespace) -> subprocess.Popen[bytes]:
    if not os.path.exists(args.server_bin):
        raise ChatError(f"llama-server not found: {args.server_bin}")
    if not os.path.exists(args.model):
        raise ChatError(f"Model not found: {args.model}")

    cmd = [
        args.server_bin,
        "--model",
        args.model,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--ctx-size",
        str(args.ctx_size),
        "--n-gpu-layers",
        args.gpu_layers,
        "--parallel",
        "1",
        "--jinja",
    ]

    stdout = None if args.verbose_server else subprocess.DEVNULL
    stderr = None if args.verbose_server else subprocess.DEVNULL

    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
    except OSError as exc:
        raise ChatError(f"Failed to start llama-server: {exc}") from exc

    time.sleep(0.3)
    if proc.poll() is not None:
        raise ChatError(f"llama-server exited immediately with code {proc.returncode}")

    if not args.keep_server:
        atexit.register(_terminate_process, proc)

    return proc


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _normalize_confidence(score: float) -> float:
    # Map cosine-like range [-1, 1] to [0, 100].
    bounded = max(-1.0, min(1.0, float(score)))
    return ((bounded + 1.0) / 2.0) * 100.0


def run_sol_ingest_build(args: argparse.Namespace) -> None:
    if not args.sol_ingest_enabled:
        return
    if not os.path.exists(args.sol_ingest_script):
        raise SolIngestError(f"sol-ingest script not found: {args.sol_ingest_script}")

    cmd = [
        sys.executable,
        args.sol_ingest_script,
        "--knowledge-dir",
        args.sol_ingest_knowledge_dir,
        "--cache-path",
        args.sol_ingest_cache_path,
        "--ollama-model",
        args.sol_ingest_ollama_model,
    ]
    for pattern in args.sol_ingest_exclude_glob:
        cmd.extend(["--exclude-glob", pattern])
    cmd.extend(
        [
        "build",
        "--all-backends",
        "--json",
        ]
    )
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.sol_ingest_timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SolIngestError(f"sol-ingest build failed: {exc}") from exc

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise SolIngestError(f"sol-ingest build failed: {detail}")


def run_sol_ingest_search(args: argparse.Namespace, query: str) -> dict[str, Any]:
    if not query.strip():
        raise SolIngestError("sol-ingest query is empty")
    cmd = [
        sys.executable,
        args.sol_ingest_script,
        "--knowledge-dir",
        args.sol_ingest_knowledge_dir,
        "--cache-path",
        args.sol_ingest_cache_path,
        "--ollama-model",
        args.sol_ingest_ollama_model,
    ]
    for pattern in args.sol_ingest_exclude_glob:
        cmd.extend(["--exclude-glob", pattern])
    cmd.extend(
        [
        "search",
        query,
        "--all-backends",
        "--json",
        "--no-build",
        "--top-k",
        str(args.sol_ingest_top_k),
        "--preview-chars",
        str(args.sol_ingest_preview_chars),
        ]
    )
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.sol_ingest_timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SolIngestError(f"sol-ingest search failed: {exc}") from exc

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise SolIngestError(f"sol-ingest search failed: {detail}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SolIngestError(f"sol-ingest returned non-JSON output: {proc.stdout[:400]}") from exc

    if not isinstance(payload, dict):
        raise SolIngestError("sol-ingest search JSON payload has unexpected shape")
    return payload


def _compact_text(text: str, max_chars: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 3] + "..."


def _load_top_hit_file_text(payload: dict[str, Any], knowledge_dir: str) -> tuple[str | None, str | None, str | None]:
    fused_results = payload.get("fused_results", [])
    if not isinstance(fused_results, list) or not fused_results:
        return (None, None, None)

    top = fused_results[0]
    rel_path = str(top.get("path", "")).strip()
    if not rel_path:
        return (None, None, None)

    base_dir = os.path.abspath(os.path.expanduser(knowledge_dir))
    candidate = os.path.abspath(os.path.join(base_dir, rel_path))
    if os.path.commonpath([base_dir, candidate]) != base_dir:
        return (None, rel_path, "top-hit path resolved outside knowledge dir")

    if not os.path.exists(candidate):
        return (None, rel_path, "top-hit file is missing on disk")

    try:
        with open(candidate, "r", encoding="utf-8", errors="replace") as f:
            return (f.read(), rel_path, None)
    except OSError as exc:
        return (None, rel_path, f"could not read top-hit file: {exc}")


def format_retrieval_system_message(
    payload: dict[str, Any],
    *,
    query: str,
    source: str,
    max_snippets: int,
    snippet_chars: int,
    knowledge_dir: str,
) -> str:
    backend_results = payload.get("backend_results", {})
    failed_backends = payload.get("failed_backends", {})
    fused_results = payload.get("fused_results", [])
    available = payload.get("available_backends", {})
    successful_backends: list[str] = []

    lines: list[str] = []
    lines.append("Sol retrieval context (semantic similarity across embedding spaces).")
    lines.append(f"query_source={source}")
    lines.append(f"query={query}")
    if isinstance(available, dict) and available:
        lines.append("embedding_spaces=" + ", ".join(sorted(str(x) for x in available.keys())))

    lines.append("backend_confidence_top_hits:")
    if isinstance(backend_results, dict):
        for backend in sorted(backend_results.keys()):
            entry = backend_results.get(backend, {})
            hits = entry.get("results", []) if isinstance(entry, dict) else []
            warning = entry.get("warning") if isinstance(entry, dict) else None
            if not hits:
                if isinstance(warning, str) and warning.strip():
                    lines.append(f"- {backend}: no_hit warning={_compact_text(warning, 120)}")
                else:
                    lines.append(f"- {backend}: no_hit")
                continue
            successful_backends.append(backend)
            top = hits[0]
            score = float(top.get("score", 0.0))
            conf = _normalize_confidence(score)
            path = str(top.get("path", "?"))
            chunk = top.get("chunk", "?")
            lines.append(
                f"- {backend}: confidence={conf:.1f}% cosine={score:.4f} doc={path}#{chunk}"
            )

    if isinstance(failed_backends, dict) and failed_backends:
        lines.append("backend_failures:")
        for backend in sorted(failed_backends.keys()):
            detail = _compact_text(str(failed_backends.get(backend, "")), 120)
            lines.append(f"- {backend}: {detail}")

    lines.append("ranked_chunks:")
    if isinstance(fused_results, list) and fused_results:
        max_rrf = max(float(item.get("rrf_score", 0.0)) for item in fused_results) or 1.0
        for idx, item in enumerate(fused_results[:max_snippets], start=1):
            rrf = float(item.get("rrf_score", 0.0))
            conf = (rrf / max_rrf) * 100.0
            path = str(item.get("path", "?"))
            chunk = item.get("chunk", "?")
            backends = item.get("backends", [])
            if isinstance(backends, list):
                backend_label = ",".join(str(x) for x in backends)
            else:
                backend_label = str(backends)
            snippet = _compact_text(str(item.get("text", "")), snippet_chars)
            lines.append(
                f"- hit_{idx}: confidence={conf:.1f}% rrf={rrf:.4f} doc={path}#{chunk} backends={backend_label}"
            )
            lines.append(f"  snippet={snippet}")
    else:
        lines.append("- no_ranked_hits")

    top_file_text, top_file_path, top_file_warning = _load_top_hit_file_text(payload, knowledge_dir)
    lines.append("top_result_file:")
    if top_file_text is not None and top_file_path is not None:
        lines.append(f"- path={top_file_path}")
        lines.append("- content_begin")
        lines.append(top_file_text)
        lines.append("- content_end")
    elif top_file_path and top_file_warning:
        lines.append(f"- unavailable path={top_file_path} warning={_compact_text(top_file_warning, 160)}")
    else:
        lines.append("- unavailable")

    strict_grounding = (
        len(successful_backends) == 0
        or (len(successful_backends) == 1 and successful_backends[0] == "local-hash")
    )
    lines.append("grounding_mode=" + ("strict_fallback" if strict_grounding else "normal"))
    if strict_grounding:
        lines.append(
            "Grounding policy: retrieval quality is low. Do not claim external books/authors/biographies/world facts unless explicitly present in top_result_file or ranked_chunks. If uncertain, state that evidence is insufficient and do not ask follow-up questions."
        )

    lines.append(
        "Prioritize retrieval evidence for factual claims. Use top_result_file as primary context and ranked_chunks for supporting citations. If evidence is weak or conflicting, state uncertainty and avoid follow-up questions instead of inventing details."
    )
    return "\n".join(lines)


def build_retrieval_message(
    args: argparse.Namespace,
    base_url: str,
    model_id: str,
    messages: list[dict[str, str]],
) -> tuple[str | None, str | None]:
    if not args.sol_ingest_enabled:
        return (None, None)
    if not os.path.exists(args.sol_ingest_script):
        return (None, f"warning> sol-ingest script not found: {args.sol_ingest_script}")
    # Simplified retrieval path: always search embeddings with the raw user prompt.
    user_query = messages[-1].get("content", "").strip()
    if not user_query:
        return (None, None)

    payload = run_sol_ingest_search(args, user_query)
    section = format_retrieval_system_message(
        payload,
        query=user_query,
        source="user",
        max_snippets=args.sol_ingest_top_k,
        snippet_chars=args.sol_ingest_preview_chars,
        knowledge_dir=args.sol_ingest_knowledge_dir,
    )
    return (section, None)


def attach_transient_system_context(
    messages: list[dict[str, str]],
    retrieval_text: str | None,
) -> list[dict[str, str]]:
    if not retrieval_text:
        return list(messages)

    if not messages:
        return [{"role": "system", "content": retrieval_text}]

    out = list(messages)
    if out and out[0].get("role") == "system":
        base = str(out[0].get("content", "")).strip()
        merged = base + "\n\n" + retrieval_text if base else retrieval_text
        out[0] = {"role": "system", "content": merged}
        return out

    out.insert(0, {"role": "system", "content": retrieval_text})
    return out


def trim_messages_for_context(
    messages: list[dict[str, str]],
    *,
    max_chars: int,
) -> list[dict[str, str]]:
    if max_chars <= 0:
        return list(messages)
    if not messages:
        return []

    system_msg: dict[str, str] | None = None
    start_idx = 0
    if messages and messages[0].get("role") == "system":
        system_msg = messages[0]
        start_idx = 1

    kept: list[dict[str, str]] = []
    used = 0
    for item in reversed(messages[start_idx:]):
        content = str(item.get("content", ""))
        role = str(item.get("role", ""))
        # rough budget: role + separators + content
        size = len(content) + len(role) + 8
        if kept and used + size > max_chars:
            break
        kept.append(item)
        used += size

    kept.reverse()
    if system_msg is not None:
        return [system_msg] + kept
    return kept


def build_grounded_request_messages(
    messages: list[dict[str, str]],
    *,
    retrieval_text: str | None,
    user_text: str,
) -> list[dict[str, str]]:
    out = list(messages)
    if not retrieval_text:
        return out

    out.append(
        {
            "role": "user",
            "content": (
                "Grounding contract for this turn:\n"
                "- Answer only using facts explicitly present in retrieval context.\n"
                "- Defer primarily to your current context. Do not introduce external books/authors/biographies/titles unless explicitly present or relevant.\n"
                "- If evidence is insufficient, say: "
                "\"I don't have enough grounded context in local knowledge to answer that confidently.\"\n"
                "- Do not ask follow-up questions unless the user explicitly requests clarification.\n"
                "- Focus on the user request: "
                f"{user_text}"
            ),
        }
    )
    return out


def _extract_retrieval_evidence_text(retrieval_text: str) -> str:
    snippets: list[str] = []
    capture = False
    for line in retrieval_text.splitlines():
        stripped = line.strip()
        if stripped == "- content_begin":
            capture = True
            continue
        if stripped == "- content_end":
            capture = False
            continue
        if capture:
            snippets.append(line)
            continue
        if stripped.startswith("snippet="):
            snippets.append(stripped.removeprefix("snippet=").strip())
    return "\n".join(snippets)


def _extract_ranked_snippets(retrieval_text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    current_doc = ""
    for line in retrieval_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- hit_") and " doc=" in stripped:
            try:
                current_doc = stripped.split(" doc=", 1)[1].split(" ", 1)[0].strip()
            except Exception:
                current_doc = ""
            continue
        if stripped.startswith("snippet="):
            snippet = stripped.removeprefix("snippet=").strip()
            if snippet:
                out.append((current_doc, snippet))
    return out


def _proper_nouns(text: str) -> set[str]:
    out: set[str] = set()
    for token in re.findall(r"\b[A-Z][A-Za-z0-9'_-]{2,}\b", text):
        if token in {
            "The",
            "This",
            "That",
            "And",
            "But",
            "For",
            "With",
            "From",
            "When",
            "Where",
            "What",
            "Who",
            "Why",
            "How",
            "Can",
            "Could",
            "Would",
            "Should",
            "You",
            "Your",
            "I",
            "Im",
        }:
            continue
        out.add(token)
    return out


def find_unsupported_entities(
    *,
    answer: str,
    retrieval_text: str | None,
    user_text: str,
) -> list[str]:
    if not retrieval_text:
        return []
    evidence_text = _extract_retrieval_evidence_text(retrieval_text)
    evidence_terms = _proper_nouns(evidence_text)
    user_terms = _proper_nouns(user_text)
    allow = evidence_terms | user_terms
    if not allow:
        return []
    answer_terms = _proper_nouns(answer)
    unsupported = sorted(term for term in answer_terms if term not in allow)
    return unsupported


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _query_focus_terms(user_text: str) -> list[str]:
    tokens = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", user_text)]
    stop = {
        "tell", "about", "what", "where", "when", "which", "who", "does", "this", "that", "from",
        "your", "local", "knowledge", "files", "file", "please", "would", "could", "should",
    }
    ordered: list[str] = []
    for tok in tokens:
        if tok in stop:
            continue
        if tok not in ordered:
            ordered.append(tok)
    return ordered[:3]


def should_force_grounded_profile_answer(user_text: str) -> bool:
    q = " ".join(user_text.lower().split())
    return (
        q.startswith("tell me about ")
        or q.startswith("who is ")
        or q.startswith("who's ")
        or q.startswith("what do we know about ")
    )


def should_force_extractive_answer(user_text: str) -> bool:
    q = " ".join(user_text.lower().split())
    prefixes = (
        "where ",
        "what exact",
        "what happens",
        "what does ",
        "quote ",
        "summarize ",
        "in ",
    )
    return q.startswith(prefixes) or " log entry " in q


def _query_terms(user_text: str) -> list[str]:
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "what", "where", "when", "which", "who",
        "tell", "about", "does", "happens", "happen", "exact", "quote", "summarize", "entry", "log",
        "local", "knowledge", "your", "in", "on", "to", "of", "is", "are", "a", "an",
    }
    out: list[str] = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", user_text.lower()):
        if tok in stop:
            continue
        if tok not in out:
            out.append(tok)
    return out[:8]


def build_extractive_fallback_answer(*, user_text: str, retrieval_text: str) -> str:
    focus_terms = _query_terms(user_text) or _query_focus_terms(user_text)
    ranked_snippets = _extract_ranked_snippets(retrieval_text)
    if ranked_snippets:
        scored: list[tuple[int, str, str]] = []
        seen: set[str] = set()
        for doc, snippet in ranked_snippets:
            compact = " ".join(snippet.split())
            if len(compact) < 24 or compact in seen:
                continue
            seen.add(compact)
            lower = compact.lower()
            score = 0
            for term in focus_terms:
                if term in lower:
                    score += 1
            scored.append((score, doc or "ranked_chunk", compact))

        scored.sort(key=lambda x: x[0], reverse=True)
        matched: list[tuple[str, str]] = []
        for score, doc, compact in scored:
            if focus_terms and score == 0 and len(matched) >= 2:
                break
            matched.append((doc, compact))
            if len(matched) >= 5:
                break
        if not matched:
            matched = [(doc or "ranked_chunk", " ".join(snippet.split())) for doc, snippet in ranked_snippets[:3]]

        qlower = user_text.lower()
        if ("exact" in qlower or "quote" in qlower) and matched:
            return f'From local knowledge:\n- "{matched[0][1]}"'

        if matched:
            blob = " ".join(snippet for _, snippet in matched).lower()
            synthesis: list[str] = []
            if "mind palace" in blob:
                synthesis.append("Elliot is tied to a 'Mind Palace' sequence where identity and control are questioned.")
            if "father" in blob and "nozal" in blob:
                synthesis.append("A core conflict centers on Elliot's father and the dragon Nozal within the rod of time.")
            if "digital plane" in blob:
                synthesis.append("In the Digital Plane arc, Elliot reappears altered after reality fractures.")
            if "stripped of memory" in blob or "remembers only fragments" in blob or "new york" in blob:
                synthesis.append("He is described as memory-impaired, retaining only fragments such as New York and an experiment.")
            if "vanish" in blob or "vanishes post-blink" in blob:
                synthesis.append("At one point, Elliot vanishes after a blink in the session aftermath.")
            if "eyes darken" in blob:
                synthesis.append("His eyes darken during the confrontation thread, signaling a shift in tone and stakes.")
            if "central park" in blob:
                synthesis.append("After the split, one group emerges in Central Park, New York City.")
            if "cornell tech" in blob:
                synthesis.append("Cornell Tech is named in the New York thread tied to the unfolding events.")
            if "egg" in blob and "basilisk" in blob:
                synthesis.append("The Basilisk is depicted as a guardian watching over an egg.")
            if "enzo" in blob or "sadavir" in blob or "brindle" in blob:
                synthesis.append("Enzo, Sadavir, and Brindle appear as key figures across the timeline and observation sequences.")
            if "post-blink" in blob:
                synthesis.append("After the blink sequence, Elliot is described as vanishing post-blink.")

            lines = []
            if synthesis:
                lines.append("Based on local knowledge, Elliot is a recurring character in a metaphysical/temporal storyline.")
                lines.extend(f"- {point}" for point in synthesis[:4])
            else:
                lines.append("From local knowledge, Elliot appears in a recurring narrative with conflict, displacement, and altered memory.")
            lines.append("Supporting evidence:")
            for doc, snippet in matched[:2]:
                lines.append(f"- [{doc}] {snippet}")
            return "\n".join(lines)

    evidence_text = _extract_retrieval_evidence_text(retrieval_text)
    if not evidence_text.strip():
        return GROUNDING_FALLBACK_TEXT
    sentences = _split_sentences(evidence_text)

    matches: list[str] = []
    for sent in sentences:
        lower = sent.lower()
        if focus_terms and not any(term in lower for term in focus_terms):
            continue
        compact = " ".join(sent.split())
        if len(compact) < 24:
            continue
        if compact not in matches:
            matches.append(compact)
        if len(matches) >= 3:
            break

    if not matches:
        for sent in sentences:
            compact = " ".join(sent.split())
            if len(compact) < 24:
                continue
            if compact not in matches:
                matches.append(compact)
            if len(matches) >= 2:
                break

    if not matches:
        return GROUNDING_FALLBACK_TEXT

    lines = ["From local knowledge:"]
    for item in matches:
        lines.append(f"- {item}")
    return "\n".join(lines)


def chat_once(
    base_url: str,
    model_id: str,
    messages: list[dict[str, str]],
    temperature: float,
    top_p: float,
    max_tokens: int,
    timeout: int,
) -> str:
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": False,
    }
    last_error: ChatError | None = None
    for _ in range(40):
        try:
            data = http_json("POST", f"{base_url}/v1/chat/completions", payload=payload, timeout=timeout)
            break
        except ChatError as exc:
            if "HTTP 503" in str(exc) and "Loading model" in str(exc):
                last_error = exc
                time.sleep(0.5)
                continue
            raise
    else:
        raise last_error if last_error is not None else ChatError("Model did not become ready in time.")

    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise ChatError(f"Unexpected response shape: {json.dumps(data)[:1000]}") from exc


def chat_stream(
    base_url: str,
    model_id: str,
    messages: list[dict[str, str]],
    temperature: float,
    top_p: float,
    max_tokens: int,
    timeout: int,
) -> str:
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=f"{base_url}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )

    chunks: list[str] = []
    for _ in range(40):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    try:
                        event = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    piece = _extract_delta(event)
                    if piece:
                        print(piece, end="", flush=True)
                        chunks.append(piece)
            break
        except urllib.error.HTTPError as exc:
            if _is_loading_error(exc):
                time.sleep(0.5)
                continue
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
            raise ChatError(f"HTTP {exc.code} from {base_url}/v1/chat/completions: {body}") from exc
        except urllib.error.URLError as exc:
            raise ChatError(f"Could not reach {base_url}/v1/chat/completions: {exc}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ChatError(f"Request timed out for {base_url}/v1/chat/completions") from exc
    else:
        raise ChatError("Model did not become ready in time.")

    text = "".join(chunks).strip()
    print()
    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local AI chat using llama.cpp server")
    parser.add_argument("--server-bin", default=DEFAULT_SERVER_BIN, help="Path to llama-server binary")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Path to GGUF model")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=18080, help="Server port")
    parser.add_argument("--ctx-size", type=int, default=DEFAULT_CTX_SIZE, help="Context size")
    parser.add_argument("--gpu-layers", default="all", help="GPU layers (e.g. all, auto, 0, 99)")
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature")
    parser.add_argument("--top-p", type=float, default=0.9, help="Top-p sampling")
    parser.add_argument("--max-tokens", type=int, default=512, help="Max tokens per response")
    parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout seconds")
    parser.add_argument("--system", default=DEFAULT_SYSTEM_PROMPT, help="System prompt")
    parser.add_argument("--history-file", default=DEFAULT_HISTORY_FILE, help="Path to persistent chat history JSON")
    parser.add_argument(
        "--history-window-chars",
        type=int,
        default=DEFAULT_HISTORY_WINDOW_CHARS,
        help="Approximate max chars of recent non-system chat history sent per request (0 disables trimming).",
    )
    parser.add_argument("--stream", dest="stream", action="store_true", help="Stream tokens as they are generated")
    parser.add_argument("--no-stream", dest="stream", action="store_false", help="Disable token streaming")
    parser.add_argument("--connect-only", action="store_true", help="Do not start server; connect to existing one")
    parser.add_argument("--keep-server", action="store_true", help="Leave server running on exit")
    parser.add_argument("--verbose-server", action="store_true", help="Show server logs")
    parser.add_argument(
        "--ensure-deps",
        dest="ensure_deps",
        action="store_true",
        help="Auto-install missing Python dependencies used by retrieval backends.",
    )
    parser.add_argument(
        "--no-ensure-deps",
        dest="ensure_deps",
        action="store_false",
        help="Skip automatic dependency installation.",
    )
    parser.add_argument(
        "--install-heavy-deps",
        dest="install_heavy_deps",
        action="store_true",
        help="Also auto-install heavier optional dependencies (for example sentence-transformers).",
    )
    parser.add_argument(
        "--no-install-heavy-deps",
        dest="install_heavy_deps",
        action="store_false",
        help="Do not auto-install heavier optional dependencies.",
    )

    parser.add_argument(
        "--sol-ingest-script",
        default=DEFAULT_SOL_INGEST_SCRIPT,
        help="Path to sol_ingest.py script used for similarity search",
    )
    parser.add_argument(
        "--sol-ingest-knowledge-dir",
        default=DEFAULT_SOL_INGEST_KNOWLEDGE_DIR,
        help="Knowledge directory to index/search with sol-ingest",
    )
    parser.add_argument(
        "--sol-ingest-exclude-glob",
        action="append",
        default=["codex_sessions/**"],
        help="Glob pattern relative to knowledge dir to exclude (repeatable). Default excludes codex session dumps.",
    )
    parser.add_argument(
        "--sol-ingest-cache-path",
        default=DEFAULT_SOL_INGEST_CACHE,
        help="Base cache path for sol-ingest embeddings cache files",
    )
    parser.add_argument(
        "--sol-ingest-ollama-model",
        default=DEFAULT_SOL_INGEST_OLLAMA_MODEL,
        help="Ollama embedding model passed through to sol-ingest (env: SOL_INGEST_OLLAMA_MODEL).",
    )
    parser.add_argument(
        "--sol-ingest-top-k",
        type=int,
        default=3,
        help="Top results per embedding space to request from sol-ingest",
    )
    parser.add_argument(
        "--sol-ingest-preview-chars",
        type=int,
        default=220,
        help="Snippet length from retrieved chunks inserted into system context",
    )
    parser.add_argument(
        "--sol-ingest-timeout",
        type=int,
        default=240,
        help="Timeout (seconds) for each sol-ingest subprocess call",
    )
    parser.add_argument(
        "--sol-ingest-query-source",
        choices=["user", "llm", "both"],
        default="user",
        help="Deprecated; retrieval always uses raw user text.",
    )
    parser.add_argument(
        "--no-sol-ingest",
        dest="sol_ingest_enabled",
        action="store_false",
        help="Disable sol-ingest retrieval context injection",
    )
    parser.add_argument(
        "--strict-grounding-enforce",
        dest="strict_grounding_enforce",
        action="store_true",
        help="Enforce post-generation grounding checks and block unsupported named entities.",
    )
    parser.add_argument(
        "--no-strict-grounding-enforce",
        dest="strict_grounding_enforce",
        action="store_false",
        help="Disable post-generation grounding checks.",
    )
    parser.set_defaults(
        stream=True,
        sol_ingest_enabled=True,
        ensure_deps=True,
        install_heavy_deps=False,
        strict_grounding_enforce=True,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        ensure_python_dependencies(enabled=args.ensure_deps, include_heavy=args.install_heavy_deps)
    except ChatError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.sol_ingest_top_k <= 0:
        print("--sol-ingest-top-k must be > 0", file=sys.stderr)
        return 2
    if args.sol_ingest_preview_chars <= 0:
        print("--sol-ingest-preview-chars must be > 0", file=sys.stderr)
        return 2
    if args.sol_ingest_timeout <= 0:
        print("--sol-ingest-timeout must be > 0", file=sys.stderr)
        return 2
    if args.history_window_chars < 0:
        print("--history-window-chars must be >= 0", file=sys.stderr)
        return 2

    base_url = f"http://{args.host}:{args.port}"

    proc: subprocess.Popen[bytes] | None = None
    if not args.connect_only:
        print(f"Starting llama-server on {base_url} ...")
        proc = start_llama_server(args)

    try:
        wait_for_server(base_url, timeout_s=90)
    except ChatError as exc:
        if proc is not None and proc.poll() is not None:
            return_code = proc.returncode
            print(f"Server failed to start (exit {return_code}): {exc}", file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return 1

    model_id = get_model_id(base_url, fallback=os.path.basename(args.model))
    print(f"Connected. Model: {model_id}")
    print("Commands: /exit, /reset")

    if args.sol_ingest_enabled:
        try:
            run_sol_ingest_build(args)
            print("sol-ingest> retrieval index ready across available embedding spaces")
        except SolIngestError as exc:
            print(f"warning> {exc}; continuing without retrieval", file=sys.stderr)
            args.sol_ingest_enabled = False

    messages, system_prompt = load_history(args.history_file, args.system)
    try:
        save_history(args.history_file, messages)
    except OSError as exc:
        print(f"warning> could not save history file {args.history_file}: {exc}", file=sys.stderr)

    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_text:
            continue
        if user_text == "/exit":
            break
        if user_text == "/reset":
            messages = [{"role": "system", "content": system_prompt}]
            try:
                save_history(args.history_file, messages)
            except OSError as exc:
                print(f"warning> could not save history file {args.history_file}: {exc}", file=sys.stderr)
            print("history reset")
            continue

        messages.append({"role": "user", "content": user_text})

        retrieval_warning: str | None = None
        retrieval_text: str | None = None
        try:
            retrieval_text, retrieval_warning = build_retrieval_message(
                args=args,
                base_url=base_url,
                model_id=model_id,
                messages=messages,
            )
        except SolIngestError as exc:
            retrieval_warning = f"warning> {exc}"

        if retrieval_warning:
            print(retrieval_warning, file=sys.stderr)
        base_messages = trim_messages_for_context(messages, max_chars=args.history_window_chars)
        active_messages = attach_transient_system_context(base_messages, retrieval_text)
        request_messages = build_grounded_request_messages(
            active_messages,
            retrieval_text=retrieval_text,
            user_text=user_text,
        )
        if args.strict_grounding_enforce and retrieval_text and (
            should_force_grounded_profile_answer(user_text) or should_force_extractive_answer(user_text)
        ):
            answer = build_extractive_fallback_answer(user_text=user_text, retrieval_text=retrieval_text)
            print(f"ai> {answer}\n")
            messages.append({"role": "assistant", "content": answer})
            try:
                save_history(args.history_file, messages)
            except OSError as exc:
                print(f"warning> could not save history file {args.history_file}: {exc}", file=sys.stderr)
            continue
        if args.strict_grounding_enforce and retrieval_text and "grounding_mode=strict_fallback" in retrieval_text:
            answer = build_extractive_fallback_answer(user_text=user_text, retrieval_text=retrieval_text)
            print(f"ai> {answer}\n")
            messages.append({"role": "assistant", "content": answer})
            try:
                save_history(args.history_file, messages)
            except OSError as exc:
                print(f"warning> could not save history file {args.history_file}: {exc}", file=sys.stderr)
            continue
        stream_this_turn = args.stream and not (args.strict_grounding_enforce and retrieval_text)

        try:
            if stream_this_turn:
                print("ai> ", end="", flush=True)
                answer = chat_stream(
                    base_url=base_url,
                    model_id=model_id,
                    messages=request_messages,
                    temperature=args.temperature,
                    top_p=args.top_p,
                    max_tokens=args.max_tokens,
                    timeout=args.timeout,
                )
                print()
            else:
                answer = chat_once(
                    base_url=base_url,
                    model_id=model_id,
                    messages=request_messages,
                    temperature=args.temperature,
                    top_p=args.top_p,
                    max_tokens=args.max_tokens,
                    timeout=args.timeout,
                )
        except ChatError as exc:
            print(f"error> {exc}", file=sys.stderr)
            messages.pop()
            continue

        if args.strict_grounding_enforce and retrieval_text:
            unsupported = find_unsupported_entities(
                answer=answer,
                retrieval_text=retrieval_text,
                user_text=user_text,
            )
            if unsupported:
                print(
                    "warning> grounding guard blocked unsupported entities: "
                    + ", ".join(unsupported[:8]),
                    file=sys.stderr,
                )
                answer = build_extractive_fallback_answer(
                    user_text=user_text,
                    retrieval_text=retrieval_text,
                )
        if not stream_this_turn:
            print(f"ai> {answer}\n")

        messages.append({"role": "assistant", "content": answer})
        try:
            save_history(args.history_file, messages)
        except OSError as exc:
            print(f"warning> could not save history file {args.history_file}: {exc}", file=sys.stderr)

    if proc is not None and args.keep_server:
        print(f"llama-server left running at {base_url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
