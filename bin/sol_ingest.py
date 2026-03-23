#!/usr/bin/env python3
"""
SOL Ingestion Runtime

Independent dataset ingestion + embeddings index + semantic search.

This is extracted from MasterBot's SolEngine and made interface-agnostic.

Usage:

    python sol_ingest.py build
    python sol_ingest.py search "your query"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pickle
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover
    SentenceTransformer = None  # type: ignore[assignment]

# -------------------------
# Paths
# -------------------------

ROOT = Path(".")
KNOWLEDGE_DIR = ROOT / "knowledge"
CACHE_PATH = ROOT / "logs" / "sol_embeddings.pkl"
DEFAULT_OLLAMA_MODEL = os.getenv("SOL_INGEST_OLLAMA_MODEL", "mxbai-embed-large")


# -------------------------
# Engine
# -------------------------


class SolIndex:
    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        backend: str = "openai",
        local_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        ollama_url: str = "http://127.0.0.1:11434",
        ollama_model: str = DEFAULT_OLLAMA_MODEL,
        local_dim: int = 512,
        embed_batch_size: int = 64,
        chunk_size: int = 900,
        overlap: int = 120,
        knowledge_dir: Path = KNOWLEDGE_DIR,
        extra_knowledge_dirs: Optional[List[Path]] = None,
        cache_path: Path = CACHE_PATH,
        exclude_globs: Optional[List[str]] = None,
    ) -> None:
        self._client = None
        self._local_embedder = None
        self.model = model
        self.backend = backend
        self.local_model = local_model
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self.local_dim = local_dim
        self.embed_batch_size = embed_batch_size
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.knowledge_dir = knowledge_dir
        self.extra_knowledge_dirs = [Path(p) for p in (extra_knowledge_dirs or [])]
        self.cache_path = cache_path
        self.exclude_globs = exclude_globs or []
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.docs: List[Dict[str, Any]] = []
        self.embeddings: List[List[float]] = []
        self.last_build_stats: Optional[Dict[str, Any]] = None

    @property
    def client(self):
        if self._client is None:
            if OpenAI is None:
                raise RuntimeError(
                    "Missing dependency: install the 'openai' package to use sol_ingest.py"
                )
            self._client = OpenAI()
        return self._client

    @property
    def local_embedder(self):
        if self._local_embedder is None:
            if SentenceTransformer is None:
                raise RuntimeError(
                    "Missing dependency: install 'sentence-transformers' to use --backend local-st"
                )
            self._local_embedder = SentenceTransformer(self.local_model)
        return self._local_embedder

    # ---------- discovery

    def discover_files(self) -> List[Path]:
        roots = [self.knowledge_dir] + self.extra_knowledge_dirs
        out: List[Path] = []
        seen: set[str] = set()
        for root in roots:
            if not root.exists():
                print(f"{root} missing")
                continue
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in {".txt", ".md", ".markdown"}:
                    continue
                if self.exclude_globs:
                    rel = p.relative_to(root).as_posix()
                    if any(Path(rel).match(pattern) for pattern in self.exclude_globs):
                        continue
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                out.append(p)
        return out

    def path_key(self, path: Path) -> str:
        path = Path(path)
        if not path.is_absolute():
            return path.as_posix()
        try:
            return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
        except Exception:
            return path.resolve().as_posix()

    def cache_lookup_keys(self, path: Path) -> List[str]:
        keys: List[str] = []
        canonical = self.path_key(path)
        keys.append(canonical)
        raw = str(path)
        if raw not in keys:
            keys.append(raw)
        try:
            resolved = str(path.resolve())
            if resolved not in keys:
                keys.append(resolved)
        except Exception:
            pass
        return keys

    def load_texts(self) -> List[Tuple[Path, float, str]]:
        corpus: List[Tuple[Path, float, str]] = []
        for p in self.discover_files():
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    corpus.append((p, p.stat().st_mtime, text))
            except Exception:
                pass
        return corpus

    # ---------- chunking

    def chunk(self, text: str) -> List[str]:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if self.overlap < 0:
            raise ValueError("overlap must be >= 0")
        if self.overlap >= self.chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")

        step = self.chunk_size - self.overlap
        chunks: List[str] = []
        i = 0
        while i < len(text):
            chunks.append(text[i : i + self.chunk_size])
            i += step
        return chunks

    # ---------- embeddings

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        if self.backend == "openai":
            result = self.client.embeddings.create(model=self.model, input=texts)
            return [list(x.embedding) for x in result.data]
        if self.backend == "ollama":
            # Prefer modern batch endpoint, but fall back to older /api/embeddings.
            url = f"{self.ollama_url}/api/embed"
            body = json.dumps({"model": self.ollama_model, "input": texts}).encode("utf-8")
            req = urllib.request.Request(
                url=url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                if isinstance(payload, dict) and isinstance(payload.get("embeddings"), list):
                    embs = payload["embeddings"]
                    return [[float(x) for x in emb] for emb in embs]
                if isinstance(payload, dict) and isinstance(payload.get("embedding"), list):
                    return [[float(x) for x in payload["embedding"]]]
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    try:
                        detail = exc.read().decode("utf-8", errors="replace").strip()
                    except Exception:
                        detail = ""
                    raise RuntimeError(
                        f"Ollama embed request failed: {exc}. {detail}".strip()
                    ) from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(
                    f"Ollama embed request failed: {exc}. Is ollama running at {self.ollama_url}?"
                ) from exc
            except Exception as exc:
                raise RuntimeError(f"Ollama embed request failed: {exc}") from exc

            legacy_url = f"{self.ollama_url}/api/embeddings"
            out: List[List[float]] = []
            for t in texts:
                legacy_body = json.dumps({"model": self.ollama_model, "prompt": t}).encode("utf-8")
                legacy_req = urllib.request.Request(
                    url=legacy_url,
                    data=legacy_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(legacy_req, timeout=120) as resp:
                        legacy_payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                except urllib.error.HTTPError as exc:
                    try:
                        detail = exc.read().decode("utf-8", errors="replace").strip()
                    except Exception:
                        detail = ""
                    raise RuntimeError(
                        f"Ollama embed request failed: {exc}. {detail}".strip()
                    ) from exc
                except urllib.error.URLError as exc:
                    raise RuntimeError(
                        f"Ollama embed request failed: {exc}. Is ollama running at {self.ollama_url}?"
                    ) from exc
                except Exception as exc:
                    raise RuntimeError(f"Ollama embed request failed: {exc}") from exc

                emb = legacy_payload.get("embedding") if isinstance(legacy_payload, dict) else None
                if not isinstance(emb, list):
                    raise RuntimeError("Ollama embed response missing 'embedding'")
                out.append([float(x) for x in emb])
            return out
        if self.backend == "local-st":
            arr = self.local_embedder.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            return [row.astype("float32").tolist() for row in arr]
        if self.backend == "local-hash":
            return [self.local_hash_embed(t) for t in texts]
        raise RuntimeError(f"Unsupported backend: {self.backend}")

    def embed_texts(
        self,
        texts: List[str],
        *,
        verbose: bool = False,
        progress_label: str = "",
        path: str = "",
    ) -> List[List[float]]:
        if not texts:
            return []
        if self.embed_batch_size <= 0:
            raise ValueError("embed_batch_size must be > 0")

        out: List[List[float]] = []
        total = len(texts)
        for start in range(0, total, self.embed_batch_size):
            end = min(start + self.embed_batch_size, total)
            batch = texts[start:end]
            if verbose:
                prefix = f"[{progress_label}] " if progress_label else ""
                where = f" {path}" if path else ""
                print(
                    f"{prefix}embed batch {start + 1}-{end}/{total}{where}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                vecs = self.embed_batch(batch)
            except Exception as exc:
                where = f" for {path}" if path else ""
                raise RuntimeError(
                    f"Embedding failed at batch {start + 1}-{end}/{total}{where}: {exc}"
                ) from exc
            out.extend(vecs)
        return out

    def local_hash_embed(self, text: str) -> List[float]:
        # Lightweight local embedding fallback: signed hashing over token counts.
        vec = [0.0] * self.local_dim
        for tok in re.findall(r"[a-z0-9_]{2,}", text.lower()):
            h = hashlib.sha1(tok.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self.local_dim
            sign = 1.0 if (h[4] & 1) == 0 else -1.0
            weight = 1.0 + ((h[5] % 7) / 10.0)
            vec[idx] += sign * weight
        mag = math.sqrt(sum(x * x for x in vec))
        if mag > 0:
            vec = [x / mag for x in vec]
        return vec

    # ---------- build

    def build(
        self,
        *,
        quiet: bool = False,
        verbose: bool = False,
        progress_label: str = "",
    ) -> Dict[str, Any]:
        start = time.time()
        corpus = self.load_texts()

        try:
            cache = (
                pickle.loads(self.cache_path.read_bytes()) if self.cache_path.exists() else {}
            )
        except Exception:
            cache = {}

        cache_meta = cache.get("meta", {})
        current_meta = {
            "backend": self.backend,
            "model": self.model,
            "local_model": self.local_model,
            "ollama_url": self.ollama_url,
            "ollama_model": self.ollama_model,
            "local_dim": self.local_dim,
            "embed_batch_size": self.embed_batch_size,
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
            "knowledge_dirs": [self.path_key(self.knowledge_dir)]
            + [self.path_key(p) for p in self.extra_knowledge_dirs],
            "exclude_globs": sorted(self.exclude_globs),
        }
        cache_docs = cache.get("docs", {})
        if cache_meta != current_meta:
            # Avoid reusing stale vectors when backend/model/chunking config changes.
            cache_docs = {}
        current_paths = {self.path_key(path) for path, _, _ in corpus}
        removed_paths = sorted(path for path in cache_docs if path not in current_paths)

        new_docs: List[Dict[str, Any]] = []
        new_vecs: List[List[float]] = []
        file_statuses: List[Dict[str, Any]] = []
        files_new = 0
        files_updated = 0
        files_cached = 0
        chunks_embedded_this_run = 0
        chunks_reused_cache = 0

        total_files = len(corpus)
        for file_i, (path_obj, mtime, text) in enumerate(corpus, start=1):
            path = self.path_key(path_obj)
            cache_entry = None
            cache_key_used = None
            for candidate in self.cache_lookup_keys(path_obj):
                if candidate in cache_docs:
                    cache_entry = cache_docs[candidate]
                    cache_key_used = candidate
                    break

            if cache_entry and cache_entry.get("mtime") == mtime:
                cached_entries = cache_entry.get("entries", [])
                for item in cached_entries:
                    new_docs.append(item["doc"])
                    new_vecs.append(item["embedding"])
                files_cached += 1
                chunks_reused_cache += len(cached_entries)
                if verbose:
                    prefix = f"[{progress_label}] " if progress_label else ""
                    print(
                        f"{prefix}{file_i}/{total_files} cached {path} "
                        f"(chunks={len(cached_entries)}, reused_total={chunks_reused_cache})",
                        file=sys.stderr,
                    )
                if cache_key_used and cache_key_used != path:
                    cache_docs[path] = cache_entry
                file_statuses.append(
                    {
                        "path": path,
                        "status": "cached",
                        "chunks": len(cached_entries),
                    }
                )
                continue

            chunks = self.chunk(text)
            if verbose:
                prefix = f"[{progress_label}] " if progress_label else ""
                print(
                    f"{prefix}{file_i}/{total_files} embedding {path} (chunks={len(chunks)})",
                    file=sys.stderr,
                )
            vecs = self.embed_texts(
                chunks,
                verbose=verbose,
                progress_label=progress_label,
                path=path,
            )
            status = "new" if cache_entry is None else "updated"

            packaged = []
            for i, (chunk_text, vec) in enumerate(zip(chunks, vecs)):
                doc = {
                    "path": path,
                    "chunk": i,
                    # Keep both keys for compatibility with MasterBot's legacy cache readers.
                    "chunk_index": i,
                    "text": chunk_text,
                    "id": hashlib.sha1(f"{path}:{i}".encode()).hexdigest()[:12],
                }
                packaged.append({"doc": doc, "embedding": vec})
                new_docs.append(doc)
                new_vecs.append(vec)

            cache_docs[path] = {"mtime": mtime, "entries": packaged}
            if status == "new":
                files_new += 1
            else:
                files_updated += 1
            chunks_embedded_this_run += len(packaged)
            if verbose:
                prefix = f"[{progress_label}] " if progress_label else ""
                print(
                    f"{prefix}{file_i}/{total_files} done {path} "
                    f"(embedded_total={chunks_embedded_this_run})",
                    file=sys.stderr,
                )
            file_statuses.append(
                {
                    "path": path,
                    "status": status,
                    "chunks": len(packaged),
                }
            )

        # Drop cache entries for files that no longer exist.
        cache_docs = {path: entry for path, entry in cache_docs.items() if path in current_paths}
        self.cache_path.write_bytes(pickle.dumps({"meta": current_meta, "docs": cache_docs}))

        self.docs = new_docs
        self.embeddings = new_vecs

        elapsed = round(time.time() - start, 2)
        stats = {
            "backend": self.backend,
            "local_model": self.local_model if self.backend == "local-st" else None,
            "ollama_model": self.ollama_model if self.backend == "ollama" else None,
            "embed_batch_size": self.embed_batch_size,
            "knowledge_dirs": [self.path_key(self.knowledge_dir)]
            + [self.path_key(p) for p in self.extra_knowledge_dirs],
            "files_total": len(corpus),
            "files_new": files_new,
            "files_updated": files_updated,
            "files_cached": files_cached,
            "files_removed": len(removed_paths),
            "chunks_indexed_total": len(new_docs),
            "chunks_embedded_this_run": chunks_embedded_this_run,
            "chunks_reused_cache": chunks_reused_cache,
            "cache_path": str(self.cache_path),
            "elapsed_s": elapsed,
            "changed_files": [f for f in file_statuses if f["status"] in {"new", "updated"}],
            "removed_files": removed_paths,
            "file_statuses": file_statuses,
        }
        self.last_build_stats = stats
        if not quiet:
            print(
                f"Indexed {len(new_docs)} chunks from {len(corpus)} files in {elapsed}s "
                f"(embedded now: {chunks_embedded_this_run}, cache reused: {chunks_reused_cache}, "
                f"files new/updated/cached/removed: {files_new}/{files_updated}/{files_cached}/{len(removed_paths)}) "
                f"(cache: {self.cache_path})"
            )
        return stats

    # ---------- search

    def load_cached_index(self) -> bool:
        if not self.cache_path.exists():
            return False
        try:
            cache = pickle.loads(self.cache_path.read_bytes())
        except Exception:
            return False
        if not isinstance(cache, dict):
            return False
        cache_docs = cache.get("docs", {})
        if not isinstance(cache_docs, dict):
            return False

        docs: List[Dict[str, Any]] = []
        embeddings: List[List[float]] = []
        for entry in cache_docs.values():
            if not isinstance(entry, dict):
                continue
            items = entry.get("entries", [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                doc = item.get("doc")
                emb = item.get("embedding")
                if isinstance(doc, dict) and isinstance(emb, list):
                    docs.append(doc)
                    embeddings.append([float(x) for x in emb])
        self.docs = docs
        self.embeddings = embeddings
        return bool(self.docs and self.embeddings)

    @staticmethod
    def cos(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        ma = math.sqrt(sum(x * x for x in a))
        mb = math.sqrt(sum(x * x for x in b))
        if ma == 0 or mb == 0:
            return 0.0
        return dot / (ma * mb)

    def search(self, query: str, k: int = 4):
        if not self.docs:
            return []
        q = self.embed_batch([query])[0]
        scored = []
        for doc, embedding in zip(self.docs, self.embeddings):
            scored.append((self.cos(q, embedding), doc))

        scored.sort(reverse=True, key=lambda x: x[0])
        return scored[:k]


# -------------------------
# CLI
# -------------------------


def is_ollama_reachable(base_url: str, timeout_s: float = 2.0) -> bool:
    url = f"{base_url.rstrip('/')}/api/tags"
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= int(getattr(resp, "status", 0)) < 300
    except Exception:
        return False


def get_ollama_models(base_url: str, timeout_s: float = 3.0) -> List[str]:
    url = f"{base_url.rstrip('/')}/api/tags"
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    models = payload.get("models", []) if isinstance(payload, dict) else []
    out: List[str] = []
    if isinstance(models, list):
        for m in models:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or "").strip()
            model = str(m.get("model") or "").strip()
            if name:
                out.append(name)
            if model and model not in out:
                out.append(model)
    return out


def choose_ollama_embedding_model(models: List[str]) -> Optional[str]:
    if not models:
        return None
    canonical = [m.strip() for m in models if m and m.strip()]
    if not canonical:
        return None
    preferred = [
        "nomic-embed-text",
        "mxbai-embed-large",
        "all-minilm",
        "bge-m3",
        "snowflake-arctic-embed",
    ]
    lowered = {m.lower(): m for m in canonical}
    for want in preferred:
        for key, raw in lowered.items():
            if want in key:
                return raw
    for raw in canonical:
        if "embed" in raw.lower():
            return raw
    return None


def is_local_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def maybe_start_ollama_server(args: argparse.Namespace, *, verbose: bool = False) -> bool:
    if not args.auto_start_servers:
        return False
    if not shutil.which("ollama"):
        return False
    if not is_local_base_url(args.ollama_url):
        if verbose:
            print(
                f"[ollama] skip autostart for non-local URL: {args.ollama_url}",
                file=sys.stderr,
            )
        return False
    if is_ollama_reachable(args.ollama_url, timeout_s=1.0):
        return True

    if verbose:
        print("[ollama] starting local server: ollama serve", file=sys.stderr)
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        if verbose:
            print(f"[ollama] autostart failed: {exc}", file=sys.stderr)
        return False

    deadline = time.time() + max(1.0, float(args.ollama_start_timeout))
    while time.time() < deadline:
        if is_ollama_reachable(args.ollama_url, timeout_s=1.0):
            if verbose:
                print(f"[ollama] reachable at {args.ollama_url}", file=sys.stderr)
            return True
        time.sleep(0.4)

    if verbose:
        print(
            f"[ollama] not reachable after {args.ollama_start_timeout:.1f}s at {args.ollama_url}",
            file=sys.stderr,
        )
    return False


def ensure_backend_ready(args: argparse.Namespace, backend: str, *, verbose: bool = False) -> None:
    if backend != "ollama":
        return
    if is_ollama_reachable(args.ollama_url):
        return
    started = maybe_start_ollama_server(args, verbose=verbose)
    if not started or not is_ollama_reachable(args.ollama_url):
        raise RuntimeError(
            f"Ollama backend requested but server is not reachable at {args.ollama_url}. "
            "Start it manually (`ollama serve`) or disable autostart with --no-auto-start-servers."
        )
    available_models = get_ollama_models(args.ollama_url)
    if available_models and args.ollama_model not in available_models:
        auto_model = choose_ollama_embedding_model(available_models)
        if auto_model:
            if verbose:
                print(
                    f"[ollama] requested model '{args.ollama_model}' missing; using '{auto_model}'",
                    file=sys.stderr,
                )
            args.ollama_model = auto_model
            return
        hint = ", ".join(available_models[:8])
        raise RuntimeError(
            f"Ollama model '{args.ollama_model}' is not installed. "
            f"Available models: {hint or 'none detected'}. "
            f"Install with: ollama pull {args.ollama_model}"
        )


def detect_available_backends(args: argparse.Namespace, *, auto_start: bool = False) -> Dict[str, str]:
    available: Dict[str, str] = {"local-hash": "available"}
    if OpenAI is not None and os.getenv("OPENAI_API_KEY"):
        available["openai"] = "OPENAI_API_KEY present"
    if SentenceTransformer is not None:
        available["local-st"] = "sentence-transformers installed"
    if auto_start:
        maybe_start_ollama_server(args, verbose=args.verbose)
    if shutil.which("ollama") and is_ollama_reachable(args.ollama_url):
        available["ollama"] = f"ollama reachable at {args.ollama_url}"
    return available


def detect_backend_status(args: argparse.Namespace) -> Dict[str, Dict[str, str]]:
    available = detect_available_backends(args)
    status: Dict[str, Dict[str, str]] = {}
    for backend in ["openai", "ollama", "local-st", "local-hash"]:
        if backend in available:
            status[backend] = {"status": "available", "reason": available[backend]}
            continue
        if backend == "openai":
            if OpenAI is None:
                status[backend] = {"status": "unavailable", "reason": "openai package missing"}
            elif not os.getenv("OPENAI_API_KEY"):
                status[backend] = {"status": "unavailable", "reason": "OPENAI_API_KEY missing"}
            else:
                status[backend] = {"status": "unavailable", "reason": "unknown"}
        elif backend == "ollama":
            if not shutil.which("ollama"):
                status[backend] = {"status": "unavailable", "reason": "ollama binary missing"}
            elif not is_ollama_reachable(args.ollama_url):
                status[backend] = {
                    "status": "unavailable",
                    "reason": f"ollama not reachable at {args.ollama_url}",
                }
            else:
                status[backend] = {"status": "unavailable", "reason": "unknown"}
        elif backend == "local-st":
            status[backend] = {
                "status": "unavailable",
                "reason": "sentence-transformers missing" if SentenceTransformer is None else "unknown",
            }
        else:
            status[backend] = {"status": "available", "reason": "always available"}
    return status


def cache_path_for_backend(base_cache_path: Path, backend: str) -> Path:
    if base_cache_path.suffix:
        return base_cache_path.with_name(
            f"{base_cache_path.stem}.{backend}{base_cache_path.suffix}"
        )
    return base_cache_path.with_name(f"{base_cache_path.name}.{backend}")


def doc_key(doc: Dict[str, Any]) -> str:
    chunk = doc.get("chunk", doc.get("chunk_index", "?"))
    return f"{doc.get('path', '')}#{chunk}"


def format_search_hits(results: List[Tuple[float, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rank, (score, doc) in enumerate(results, start=1):
        out.append(
            {
                "rank": rank,
                "score": float(score),
                "path": doc.get("path"),
                "chunk": doc.get("chunk", doc.get("chunk_index", "?")),
                "text": doc.get("text", ""),
                "doc_key": doc_key(doc),
            }
        )
    return out


def fuse_hits(
    per_backend_hits: Dict[str, List[Dict[str, Any]]],
    *,
    max_results: int,
) -> List[Dict[str, Any]]:
    # Reciprocal rank fusion keeps scoring stable across heterogeneous backends.
    rrf_k = 60.0
    fused: Dict[str, Dict[str, Any]] = {}
    for backend, hits in per_backend_hits.items():
        for hit in hits:
            key = str(hit.get("doc_key") or "")
            if not key:
                continue
            rank = int(hit.get("rank") or 1)
            score = 1.0 / (rrf_k + rank)
            entry = fused.get(key)
            if entry is None:
                entry = {
                    "doc_key": key,
                    "path": hit.get("path"),
                    "chunk": hit.get("chunk"),
                    "text": hit.get("text"),
                    "rrf_score": 0.0,
                    "backends": [],
                }
                fused[key] = entry
            entry["rrf_score"] = float(entry["rrf_score"]) + score
            entry["backends"].append(backend)
    ordered = sorted(
        fused.values(),
        key=lambda x: (float(x.get("rrf_score", 0.0)), len(x.get("backends", []))),
        reverse=True,
    )
    return ordered[:max_results]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build/search a local embeddings index.")
    parser.add_argument(
        "--backend",
        choices=["openai", "ollama", "local-st", "local-hash"],
        default="openai",
        help="Embedding backend: openai, ollama (local server), local-st (SentenceTransformers, local), or local-hash (fallback).",
    )
    parser.add_argument(
        "--local-model",
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="Model id/path used by --backend local-st.",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434",
        help="Base URL for Ollama API used by --backend ollama.",
    )
    parser.add_argument(
        "--ollama-model",
        default=DEFAULT_OLLAMA_MODEL,
        help="Model name used by --backend ollama.",
    )
    parser.add_argument(
        "--local-dim",
        type=int,
        default=512,
        help="Vector dimension for --backend local-hash.",
    )
    parser.add_argument("--model", default="text-embedding-3-small")
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--overlap", type=int, default=120)
    parser.add_argument("--knowledge-dir", default=str(KNOWLEDGE_DIR))
    parser.add_argument(
        "--extra-knowledge-dir",
        action="append",
        default=[],
        help="Additional directory to index alongside --knowledge-dir. Repeatable.",
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Glob pattern relative to each indexed knowledge directory to exclude from indexing/search. Repeatable.",
    )
    parser.add_argument(
        "--ignore-session-log-dir",
        action="store_true",
        help=(
            "Exclude common session-log directories/files from indexing "
            "(for example codex_sessions and sessionlog*.txt)."
        ),
    )
    parser.add_argument("--cache-path", default=str(CACHE_PATH))
    parser.add_argument(
        "--embed-batch-size",
        type=int,
        default=64,
        help="Number of chunks per embedding API call for network backends.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress details to stderr (backend starts, per-file status).",
    )
    parser.add_argument(
        "--auto-start-servers",
        dest="auto_start_servers",
        action="store_true",
        default=True,
        help="Auto-start required local backend servers when needed (currently ollama).",
    )
    parser.add_argument(
        "--no-auto-start-servers",
        dest="auto_start_servers",
        action="store_false",
        help="Disable automatic startup attempts for local backend servers.",
    )
    parser.add_argument(
        "--ollama-start-timeout",
        type=float,
        default=12.0,
        help="Seconds to wait for ollama to become reachable after autostart.",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument(
        "--json",
        action="store_true",
        help="Print build stats as JSON (machine-readable) instead of the text summary.",
    )
    build_parser.add_argument(
        "--list-changes",
        action="store_true",
        help="After build, print new/updated/removed files.",
    )
    build_parser.add_argument(
        "--all-backends",
        action="store_true",
        help="Build embeddings for all currently available backends and write backend-specific caches.",
    )

    search_parser = sub.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("-k", "--top-k", type=int, default=4)
    search_parser.add_argument("--preview-chars", type=int, default=300)
    search_parser.add_argument(
        "--json",
        action="store_true",
        help="Print search results as JSON.",
    )
    search_parser.add_argument(
        "--all-backends",
        action="store_true",
        help="Run search across all currently available embedding backends.",
    )
    search_parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip rebuilding before search (useful if running in embedded workflows).",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.chunk_size <= 0:
        parser.error("--chunk-size must be > 0")
    if args.overlap < 0:
        parser.error("--overlap must be >= 0")
    if args.overlap >= args.chunk_size:
        parser.error("--overlap must be smaller than --chunk-size")
    if args.embed_batch_size <= 0:
        parser.error("--embed-batch-size must be > 0")

    effective_exclude_globs = list(args.exclude_glob)
    if args.ignore_session_log_dir:
        effective_exclude_globs.extend(
            [
                "codex_sessions/**",
                "sessionlogs/**",
                "session_logs/**",
                "**/sessionlog*.txt",
                "**/sessionlog*.jsonl",
                "**/sessionlog*.jsonl.txt",
            ]
        )

    idx = SolIndex(
        model=args.model,
        backend=args.backend,
        local_model=args.local_model,
        ollama_url=args.ollama_url,
        ollama_model=args.ollama_model,
        local_dim=args.local_dim,
        embed_batch_size=args.embed_batch_size,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        knowledge_dir=Path(args.knowledge_dir),
        extra_knowledge_dirs=[Path(p) for p in args.extra_knowledge_dir],
        cache_path=Path(args.cache_path),
        exclude_globs=effective_exclude_globs,
    )

    try:
        if args.cmd == "build":
            if getattr(args, "all_backends", False):
                available = detect_available_backends(args, auto_start=args.auto_start_servers)
                if not available:
                    raise RuntimeError(
                        "No embedding backends are currently available."
                    )
                ordered = [b for b in ["openai", "ollama", "local-st", "local-hash"] if b in available]
                if args.verbose:
                    status = detect_backend_status(args)
                    print("Backend availability:", file=sys.stderr)
                    for backend in ["openai", "ollama", "local-st", "local-hash"]:
                        s = status[backend]
                        print(
                            f"- {backend}: {s['status']} ({s['reason']})",
                            file=sys.stderr,
                        )
                aggregate: Dict[str, Any] = {
                    "knowledge_dir": str(args.knowledge_dir),
                    "extra_knowledge_dirs": list(args.extra_knowledge_dir),
                    "knowledge_dirs": [str(args.knowledge_dir)] + list(args.extra_knowledge_dir),
                    "exclude_globs": list(effective_exclude_globs),
                    "ignore_session_log_dir": bool(args.ignore_session_log_dir),
                    "requested_at_backend": args.backend,
                    "available_backends": available,
                    "built_backends": {},
                    "failed_backends": {},
                }
                for backend in ordered:
                    try:
                        ensure_backend_ready(args, backend, verbose=args.verbose)
                        backend_cache = cache_path_for_backend(Path(args.cache_path), backend)
                        idx = SolIndex(
                            model=args.model,
                            backend=backend,
                            local_model=args.local_model,
                            ollama_url=args.ollama_url,
                            ollama_model=args.ollama_model,
                            local_dim=args.local_dim,
                            embed_batch_size=args.embed_batch_size,
                            chunk_size=args.chunk_size,
                            overlap=args.overlap,
                            knowledge_dir=Path(args.knowledge_dir),
                            extra_knowledge_dirs=[Path(p) for p in args.extra_knowledge_dir],
                            cache_path=backend_cache,
                            exclude_globs=effective_exclude_globs,
                        )
                        if args.verbose:
                            print(
                                f"[{backend}] build.start cache={backend_cache}",
                                file=sys.stderr,
                            )
                        b_start = time.time()
                        stats = idx.build(
                            quiet=True,
                            verbose=args.verbose,
                            progress_label=backend,
                        )
                        if args.verbose:
                            print(
                                f"[{backend}] build.done elapsed_s={round(time.time() - b_start, 2)} "
                                f"chunks={stats['chunks_indexed_total']}",
                                file=sys.stderr,
                            )
                        aggregate["built_backends"][backend] = stats
                    except Exception as exc:
                        aggregate["failed_backends"][backend] = str(exc)
                        if args.verbose:
                            print(f"[{backend}] build.failed {exc}", file=sys.stderr)
                        continue

                if getattr(args, "json", False):
                    print(json.dumps(aggregate, indent=2))
                else:
                    print("Built embeddings for available backends:")
                    for backend in ordered:
                        if backend in aggregate["failed_backends"]:
                            print(f"- {backend}: FAILED ({aggregate['failed_backends'][backend]})")
                            continue
                        stats = aggregate["built_backends"][backend]
                        print(
                            f"- {backend}: files={stats['files_total']} chunks={stats['chunks_indexed_total']} "
                            f"embedded_now={stats['chunks_embedded_this_run']} cache={stats['cache_path']}"
                        )
                return 0

            ensure_backend_ready(args, args.backend, verbose=args.verbose)
            if args.backend == "ollama":
                idx.ollama_model = args.ollama_model
            stats = idx.build(quiet=getattr(args, "json", False), verbose=args.verbose)
            if getattr(args, "json", False):
                print(json.dumps(stats, indent=2))
            elif getattr(args, "list_changes", False):
                changed = stats["changed_files"]
                removed = stats["removed_files"]
                if changed:
                    print("\nChanged files:")
                    for item in changed:
                        print(f"- {item['status']}: {item['path']} ({item['chunks']} chunks)")
                if removed:
                    print("\nRemoved files:")
                    for path in removed:
                        print(f"- removed: {path}")
                if not changed and not removed:
                    print("\nNo new, updated, or removed files.")
            return 0

        if args.top_k <= 0:
            parser.error("--top-k must be > 0")
        if args.preview_chars <= 0:
            parser.error("--preview-chars must be > 0")

        if getattr(args, "all_backends", False):
            available = detect_available_backends(args, auto_start=args.auto_start_servers)
            ordered = [b for b in ["openai", "ollama", "local-st", "local-hash"] if b in available]
            if not ordered:
                raise RuntimeError("No embedding backends are currently available for search.")

            if args.verbose:
                status = detect_backend_status(args)
                print("Backend availability:", file=sys.stderr)
                for backend in ["openai", "ollama", "local-st", "local-hash"]:
                    s = status[backend]
                    print(
                        f"- {backend}: {s['status']} ({s['reason']})",
                        file=sys.stderr,
                    )

            aggregate: Dict[str, Any] = {
                "query": args.query,
                "top_k": args.top_k,
                "knowledge_dir": str(args.knowledge_dir),
                "extra_knowledge_dirs": list(args.extra_knowledge_dir),
                "knowledge_dirs": [str(args.knowledge_dir)] + list(args.extra_knowledge_dir),
                "exclude_globs": list(effective_exclude_globs),
                "ignore_session_log_dir": bool(args.ignore_session_log_dir),
                "available_backends": available,
                "backend_results": {},
                "failed_backends": {},
                "fused_results": [],
            }
            per_backend_hits: Dict[str, List[Dict[str, Any]]] = {}

            for backend in ordered:
                try:
                    ensure_backend_ready(args, backend, verbose=args.verbose)
                    backend_cache = cache_path_for_backend(Path(args.cache_path), backend)
                    local_idx = SolIndex(
                        model=args.model,
                        backend=backend,
                        local_model=args.local_model,
                        ollama_url=args.ollama_url,
                        ollama_model=args.ollama_model,
                        local_dim=args.local_dim,
                        embed_batch_size=args.embed_batch_size,
                        chunk_size=args.chunk_size,
                        overlap=args.overlap,
                        knowledge_dir=Path(args.knowledge_dir),
                        extra_knowledge_dirs=[Path(p) for p in args.extra_knowledge_dir],
                        cache_path=backend_cache,
                        exclude_globs=effective_exclude_globs,
                    )
                    build_stats: Optional[Dict[str, Any]] = None
                    if not args.no_build:
                        if args.verbose:
                            print(f"[{backend}] build.start cache={backend_cache}", file=sys.stderr)
                        build_stats = local_idx.build(
                            quiet=True,
                            verbose=args.verbose,
                            progress_label=backend,
                        )
                        if args.verbose:
                            print(
                                f"[{backend}] build.done chunks={build_stats.get('chunks_indexed_total')}",
                                file=sys.stderr,
                            )
                    else:
                        loaded = local_idx.load_cached_index()
                        if args.verbose:
                            print(
                                f"[{backend}] cache.load {'ok' if loaded else 'missing'} cache={backend_cache}",
                                file=sys.stderr,
                            )
                        if not loaded:
                            aggregate["backend_results"][backend] = {
                                "cache_path": str(backend_cache),
                                "build_stats": None,
                                "results": [],
                                "warning": "cache missing or unreadable; run without --no-build",
                            }
                            per_backend_hits[backend] = []
                            continue

                    raw_results = local_idx.search(args.query, k=args.top_k)
                    hits = format_search_hits(raw_results)
                    per_backend_hits[backend] = hits
                    aggregate["backend_results"][backend] = {
                        "cache_path": str(backend_cache),
                        "build_stats": build_stats,
                        "results": hits,
                    }
                except Exception as exc:
                    aggregate["failed_backends"][backend] = str(exc)
                    per_backend_hits[backend] = []
                    aggregate["backend_results"][backend] = {
                        "cache_path": str(cache_path_for_backend(Path(args.cache_path), backend)),
                        "build_stats": None,
                        "results": [],
                        "warning": str(exc),
                    }
                    if args.verbose:
                        print(f"[{backend}] search.failed {exc}", file=sys.stderr)
                    continue

            fused = fuse_hits(per_backend_hits, max_results=args.top_k)
            aggregate["fused_results"] = fused

            if args.json:
                print(json.dumps(aggregate, indent=2))
                return 0

            print(f"Query: {args.query}")
            print("Backends:", ", ".join(ordered))
            for backend in ordered:
                print(f"\n[{backend}]")
                hits = aggregate["backend_results"][backend]["results"]
                if not hits:
                    print("No matches")
                    continue
                for hit in hits:
                    print(f"[{hit['score']:.3f}] {hit['path']}#{hit['chunk']}")
                    print(str(hit["text"])[: args.preview_chars])

            print("\n[Fused]")
            if not fused:
                print("No matches")
            else:
                for row in fused:
                    print(f"[rrf={row['rrf_score']:.4f}] {row['path']}#{row['chunk']} ({','.join(row['backends'])})")
                    print(str(row.get("text", ""))[: args.preview_chars])
            return 0

        ensure_backend_ready(args, args.backend, verbose=args.verbose)
        if args.backend == "ollama":
            idx.ollama_model = args.ollama_model
        if not args.no_build:
            idx.build()
        else:
            loaded = idx.load_cached_index()
            if args.verbose:
                print(
                    f"[{args.backend}] cache.load {'ok' if loaded else 'missing'} cache={args.cache_path}",
                    file=sys.stderr,
                )
            if not loaded:
                print(
                    "No indexed chunks available. Run `build` first or remove `--no-build`.",
                )
                return 0

        results = idx.search(args.query, k=args.top_k)
        if not results:
            print(
                "No indexed chunks available. Run `build` and ensure indexed knowledge directories "
                "contain .txt/.md files."
            )
            return 0

        if args.json:
            payload = {
                "query": args.query,
                "backend": args.backend,
                "top_k": args.top_k,
                "results": format_search_hits(results),
            }
            print(json.dumps(payload, indent=2))
            return 0

        for score, doc in results:
            chunk_label = doc.get("chunk", doc.get("chunk_index", "?"))
            print(f"\n[{score:.3f}] {doc['path']}#{chunk_label}")
            print(doc["text"][: args.preview_chars])
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
