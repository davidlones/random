#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import sqlite3
from pathlib import Path


CONTROL_SEPARATORS = {";", "|", "||", "&&", "&"}
REDIRECTION_PREFIXES = (">", "<")
WRAPPER_COMMANDS = {"command", "builtin", "env", "nice", "nohup", "time"}
PYTHON_LAUNCHERS = {"python", "python2", "python3"}


def split_segments(raw_line: str) -> list[str]:
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False
    i = 0

    while i < len(raw_line):
        ch = raw_line[i]

        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue

        if ch == "\\" and quote != "'":
            buf.append(ch)
            escaped = True
            i += 1
            continue

        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch in {"'", '"'}:
            quote = ch
            buf.append(ch)
            i += 1
            continue

        two = raw_line[i : i + 2]
        if two in {"||", "&&"}:
            segment = "".join(buf).strip()
            if segment:
                segments.append(segment)
            buf = []
            i += 2
            continue

        if ch in {";", "|", "&"}:
            segment = "".join(buf).strip()
            if segment:
                segments.append(segment)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    segment = "".join(buf).strip()
    if segment:
        segments.append(segment)
    return segments


def load_history_entries(history_path: Path) -> list[str]:
    raw_lines = history_path.read_text(encoding="utf-8", errors="replace").splitlines()
    entries: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if line[:1].isspace():
            continue
        if stripped in {')]"', ')]"', ")]", "]", ']"', '---'}:
            continue
        entries.append(line)
    return entries


def is_assignment(token: str) -> bool:
    if "=" not in token:
        return False
    if token.startswith((">", "<")):
        return False
    name = token.split("=", 1)[0]
    return bool(name) and name.replace("_", "A").isalnum() and not name[0].isdigit()


def is_noise_utility(token: str) -> bool:
    stripped = token.strip()
    if not stripped:
        return True

    data_chars = set("0123456789,().[]{}#:+-*/ ")
    if "," in stripped and all(ch in data_chars for ch in stripped):
        return True

    if stripped.startswith(("m.", ".", ")]", "]]", "],", "))")):
        return True

    if stripped.count("(") and not any(ch.isalpha() for ch in stripped.split("(", 1)[0]):
        return True

    return False


def extract_utility(segment: str) -> str | None:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return None

    i = 0
    while i < len(tokens):
        token = tokens[i]

        if is_assignment(token):
            i += 1
            continue

        if token == "sudo":
            i += 1
            while i < len(tokens):
                current = tokens[i]
                if current == "--":
                    i += 1
                    break
                if current.startswith("-"):
                    i += 1
                    if current in {"-u", "-g", "-h", "-p", "-C", "-T", "-r", "-t"}:
                        i += 1
                    continue
                break
            continue

        if token in WRAPPER_COMMANDS:
            i += 1
            while i < len(tokens) and (tokens[i].startswith("-") or is_assignment(tokens[i])):
                i += 1
            continue

        if token in PYTHON_LAUNCHERS or (
            token.startswith("python")
            and len(token) > 6
            and all(ch.isdigit() or ch == "." for ch in token[6:])
        ):
            i += 1
            while i < len(tokens):
                current = tokens[i]
                if current == "-m":
                    if i + 1 < len(tokens):
                        return current + " " + tokens[i + 1]
                    return None
                if current == "-c":
                    return "-c"
                if current == "-":
                    return "-"
                if current.startswith("-"):
                    i += 1
                    if current in {"-W", "-X"} and i < len(tokens):
                        i += 1
                    continue
                return current
            return None

        if token.startswith(REDIRECTION_PREFIXES):
            i += 1
            if token in {">", ">>", "<", "<<", "<<<", "<>", ">|"} and i < len(tokens):
                i += 1
            continue

        if token in {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "{", "}", "(", ")"}:
            return None

        if is_noise_utility(token):
            return None
        return token

    return None


def build_db(history_path: Path, output_path: Path) -> dict[str, int]:
    raw_lines = load_history_entries(history_path)
    file_hash = hashlib.sha256(history_path.read_bytes()).hexdigest()

    conn = sqlite3.connect(output_path)
    try:
        cur = conn.cursor()
        cur.executescript(
            """
            PRAGMA journal_mode=WAL;
            DROP TABLE IF EXISTS meta;
            DROP TABLE IF EXISTS history_entries;
            DROP TABLE IF EXISTS segments;
            DROP TABLE IF EXISTS utilities;
            DROP TABLE IF EXISTS utility_variations;

            CREATE TABLE meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE history_entries (
              id INTEGER PRIMARY KEY,
              line_number INTEGER NOT NULL,
              raw_line TEXT NOT NULL
            );

            CREATE TABLE segments (
              id INTEGER PRIMARY KEY,
              entry_id INTEGER NOT NULL REFERENCES history_entries(id),
              segment_index INTEGER NOT NULL,
              raw_segment TEXT NOT NULL,
              utility TEXT
            );

            CREATE TABLE utilities (
              utility TEXT PRIMARY KEY,
              invocation_count INTEGER NOT NULL,
              variation_count INTEGER NOT NULL
            );

            CREATE TABLE utility_variations (
              utility TEXT NOT NULL,
              raw_segment TEXT NOT NULL,
              invocation_count INTEGER NOT NULL,
              PRIMARY KEY (utility, raw_segment)
            );

            CREATE INDEX idx_segments_utility ON segments(utility);
            CREATE INDEX idx_segments_entry ON segments(entry_id);
            """
        )

        cur.executemany(
            "INSERT INTO meta(key, value) VALUES(?, ?)",
            [
                ("source_file", str(history_path)),
                ("source_sha256", file_hash),
                ("entry_count", str(len(raw_lines))),
            ],
        )

        history_rows: list[tuple[int, str]] = []
        segment_rows: list[tuple[int, int, str, str | None]] = []
        entry_id = 0
        segment_count = 0

        for line_number, raw_line in enumerate(raw_lines, start=1):
            entry_id += 1
            history_rows.append((line_number, raw_line))
            for segment_index, raw_segment in enumerate(split_segments(raw_line), start=1):
                utility = extract_utility(raw_segment)
                segment_rows.append((entry_id, segment_index, raw_segment, utility))
                segment_count += 1

        cur.executemany(
            "INSERT INTO history_entries(line_number, raw_line) VALUES(?, ?)",
            history_rows,
        )
        cur.executemany(
            "INSERT INTO segments(entry_id, segment_index, raw_segment, utility) VALUES(?, ?, ?, ?)",
            segment_rows,
        )

        cur.execute(
            """
            INSERT INTO utility_variations(utility, raw_segment, invocation_count)
            SELECT utility, raw_segment, COUNT(*)
            FROM segments
            WHERE utility IS NOT NULL
            GROUP BY utility, raw_segment
            """
        )
        cur.execute(
            """
            INSERT INTO utilities(utility, invocation_count, variation_count)
            SELECT utility, SUM(invocation_count), COUNT(*)
            FROM utility_variations
            GROUP BY utility
            """
        )
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM utilities")
        utility_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM utility_variations")
        variation_count = cur.fetchone()[0]
        return {
            "entry_count": len(raw_lines),
            "segment_count": segment_count,
            "utility_count": utility_count,
            "variation_count": variation_count,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory bash history into a SQLite database.")
    parser.add_argument(
        "--history",
        default=str(Path.home() / ".bash_history"),
        help="Path to the bash history file",
    )
    parser.add_argument(
        "--output",
        default=str(Path.home() / "bash_history_inventory.sqlite3"),
        help="Path to the output SQLite database",
    )
    args = parser.parse_args()

    history_path = Path(os.path.expanduser(args.history)).resolve()
    output_path = Path(os.path.expanduser(args.output)).resolve()
    summary = build_db(history_path, output_path)

    print(f"source: {history_path}")
    print(f"output: {output_path}")
    for key in ("entry_count", "segment_count", "utility_count", "variation_count"):
        print(f"{key}: {summary[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
