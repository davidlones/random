#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


VIDEO_DIR = Path("/home/david/random/www/video")
SUPPORTED_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mpg", ".mpeg", ".wmv"}


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=True)


def ffprobe_stream(path: Path) -> dict:
    result = run([
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ])
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
      raise RuntimeError(f"No video stream found in {path}")
    stream = streams[0]
    return {
        "codec": (stream.get("codec_name") or "").lower(),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float(data.get("format", {}).get("duration") or 0.0),
    }


def is_browser_safe(path: Path, probe: dict | None = None) -> bool:
    if path.suffix.lower() not in {".mp4", ".m4v"}:
        return False
    probe = probe or ffprobe_stream(path)
    return probe["codec"] == "h264"


def converted_name(path: Path) -> Path:
    return path.with_name(f"{path.stem}_h264.mp4")


def source_files(video_dir: Path) -> list[Path]:
    files = []
    for path in sorted(video_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name == "playlist.json":
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        files.append(path)
    return files


def needs_conversion(source: Path, target: Path) -> bool:
    if not target.exists():
        return True
    return source.stat().st_mtime > target.stat().st_mtime


def convert_to_h264(source: Path, target: Path) -> None:
    temp_target = target.with_suffix(".tmp.mp4")
    if temp_target.exists():
        temp_target.unlink()
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(temp_target),
    ]
    subprocess.run(command, check=True)
    temp_target.replace(target)


def canonical_key(path: Path) -> str:
    stem = path.stem
    for suffix in ("_h264", "-h264", "_hevc", "-hevc", "_h265", "-h265"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem.lower()


def score_preference(path: Path, probe: dict) -> int:
    lower_name = path.name.lower()
    score = 0
    if path.suffix.lower() in {".mp4", ".m4v"}:
        score += 10
    if probe["codec"] == "h264":
        score += 20
    if "_h264" in lower_name:
        score += 5
    return score


def load_existing_order(playlist_path: Path) -> dict[str, int]:
    if not playlist_path.exists():
        return {}
    try:
        payload = json.loads(playlist_path.read_text())
    except json.JSONDecodeError:
        return {}
    videos = payload.get("videos")
    if not isinstance(videos, list):
        return {}
    order = {}
    for index, item in enumerate(videos):
        if not isinstance(item, dict):
            continue
        file_name = item.get("file")
        if isinstance(file_name, str):
            order[file_name] = index
    return order


def refresh_playlist(video_dir: Path, verbose: bool) -> tuple[list[dict], list[str]]:
    changed = []
    probes: dict[Path, dict] = {}
    playlist_path = video_dir / "playlist.json"
    existing_order = load_existing_order(playlist_path)

    files = source_files(video_dir)
    for path in files:
        probe = ffprobe_stream(path)
        probes[path] = probe
        if path.name.lower().endswith("_h264.mp4"):
            continue
        if is_browser_safe(path, probe):
            continue

        target = converted_name(path)
        if needs_conversion(path, target):
            if verbose:
                print(f"[convert] {path.name} -> {target.name}")
            convert_to_h264(path, target)
            changed.append(target.name)
        probes[target] = ffprobe_stream(target)

    candidates = source_files(video_dir)
    preferred: dict[str, Path] = {}
    preferred_probe: dict[Path, dict] = {}

    for path in candidates:
        try:
            probe = probes.get(path) or ffprobe_stream(path)
        except Exception as exc:
            if verbose:
                print(f"[skip] {path.name}: {exc}", file=sys.stderr)
            continue
        probes[path] = probe
        key = canonical_key(path)
        current = preferred.get(key)
        if current is None:
            preferred[key] = path
            preferred_probe[path] = probe
            continue
        current_probe = preferred_probe[current]
        if score_preference(path, probe) > score_preference(current, current_probe):
            preferred[key] = path
            preferred_probe[path] = probe

    ordered_paths = sorted(
        preferred.values(),
        key=lambda item: (existing_order.get(item.name, sys.maxsize), item.name.lower()),
    )

    videos = []
    for index, path in enumerate(ordered_paths, start=1):
        probe = probes[path]
        videos.append({
            "title": f"Clip {index:02d}",
            "file": path.name,
            "codec": "h264" if probe["codec"] == "h264" else probe["codec"],
            "width": probe["width"],
            "height": probe["height"],
            "duration": round(probe["duration"], 6),
            "note": "Auto-generated from the live `/video` directory.",
        })

    payload = {"videos": videos}
    existing = None
    if playlist_path.exists():
        existing = playlist_path.read_text()
    rendered = json.dumps(payload, indent=2) + "\n"
    if rendered != existing:
        playlist_path.write_text(rendered)
        changed.append(playlist_path.name)
        if verbose:
            print(f"[write] {playlist_path}")
    elif verbose:
        print("[write] playlist.json unchanged")

    return videos, changed


def snapshot(video_dir: Path) -> tuple[tuple[str, int, int], ...]:
    state = []
    for path in source_files(video_dir):
        stat = path.stat()
        state.append((path.name, int(stat.st_mtime), stat.st_size))
    return tuple(state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch /video, convert unsupported clips, and rebuild playlist.json.")
    parser.add_argument("--dir", default=str(VIDEO_DIR), help="Video directory to watch.")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds.")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit.")
    parser.add_argument("--quiet", action="store_true", help="Only print errors.")
    args = parser.parse_args()

    video_dir = Path(args.dir).expanduser().resolve()
    if not video_dir.is_dir():
        raise SystemExit(f"Video directory does not exist: {video_dir}")
    verbose = not args.quiet
    previous = None

    while True:
        current = snapshot(video_dir)
        if current != previous:
            videos, changed = refresh_playlist(video_dir, verbose=verbose)
            if verbose:
                print(f"[state] {len(videos)} preferred clip(s), {len(changed)} file change(s)")
            previous = snapshot(video_dir)
        if args.once:
            return 0
        time.sleep(max(args.interval, 0.2))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
