from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass
class FileCursor:
    inode: int
    offset: int


class TranscriptTailer:
    def __init__(self, patterns: list[str], *, start_at_end: bool = True) -> None:
        self.patterns = patterns
        self.start_at_end = start_at_end
        self._cursors: dict[str, FileCursor] = {}

    def _matching_paths(self) -> list[str]:
        paths: set[str] = set()
        for pattern in self.patterns:
            expanded = os.path.expanduser(pattern)
            for path in glob.glob(expanded, recursive=True):
                if os.path.isfile(path):
                    paths.add(path)
        return sorted(paths)

    def _ensure_cursor(self, path: str) -> None:
        stat = os.stat(path)
        cursor = self._cursors.get(path)
        if cursor and cursor.inode == stat.st_ino:
            return
        offset = stat.st_size if self.start_at_end and cursor is None else 0
        self._cursors[path] = FileCursor(inode=stat.st_ino, offset=offset)

    def poll(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in self._matching_paths():
            self._ensure_cursor(path)
            cursor = self._cursors[path]
            try:
                stat = os.stat(path)
            except FileNotFoundError:
                continue
            if stat.st_ino != cursor.inode or stat.st_size < cursor.offset:
                cursor = FileCursor(inode=stat.st_ino, offset=0)
                self._cursors[path] = cursor
            if stat.st_size == cursor.offset:
                continue
            with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(cursor.offset)
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        payload = {"event": "log", "text": line}
                    payload["_path"] = path
                    records.append(payload)
                cursor.offset = handle.tell()
        return records


class LineTailer:
    def __init__(self, patterns: list[str], *, start_at_end: bool = True) -> None:
        self.patterns = patterns
        self.start_at_end = start_at_end
        self._cursors: dict[str, FileCursor] = {}

    def _matching_paths(self) -> list[str]:
        paths: set[str] = set()
        for pattern in self.patterns:
            expanded = os.path.expanduser(pattern)
            for path in glob.glob(expanded, recursive=True):
                if os.path.isfile(path):
                    paths.add(path)
        return sorted(paths)

    def _ensure_cursor(self, path: str) -> None:
        stat = os.stat(path)
        cursor = self._cursors.get(path)
        if cursor and cursor.inode == stat.st_ino:
            return
        offset = stat.st_size if self.start_at_end and cursor is None else 0
        self._cursors[path] = FileCursor(inode=stat.st_ino, offset=offset)

    def poll(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in self._matching_paths():
            self._ensure_cursor(path)
            cursor = self._cursors[path]
            try:
                stat = os.stat(path)
            except FileNotFoundError:
                continue
            if stat.st_ino != cursor.inode or stat.st_size < cursor.offset:
                cursor = FileCursor(inode=stat.st_ino, offset=0)
                self._cursors[path] = cursor
            if stat.st_size == cursor.offset:
                continue
            with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(cursor.offset)
                for raw_line in handle:
                    line = raw_line.rstrip("\n")
                    if not line.strip():
                        continue
                    records.append({"text": line, "_path": path})
                cursor.offset = handle.tell()
        return records
