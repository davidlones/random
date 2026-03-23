#!/usr/bin/env python3
import argparse
import io
import os
import sys
import zipfile

import imageio.v2 as imageio
from PIL import Image  # pip install pillow


def progress_bar(label, current, total, width=30):
    frac = (current / total) if total > 0 else 0.0
    frac = max(0.0, min(frac, 1.0))
    filled = int(width * frac)
    bar = "#" * filled + "-" * (width - filled)
    percent = frac * 100
    sys.stderr.write(f"\r{label:18} [{bar}] {current}/{total} ({percent:5.1f}%)")
    sys.stderr.flush()


def mov_frames_to_zip(
    input_path: str,
    output_zip: str | None = None,
    quality: int = 90,
    lossless: bool = False,
):
    # Derive default zip name
    if output_zip is None:
        base, _ = os.path.splitext(os.path.basename(input_path))
        output_zip = f"{base}_frames.zip"

    # Open reader
    reader = imageio.get_reader(input_path)
    meta = {}
    try:
        meta = reader.get_meta_data()
    except Exception:
        pass

    fps = meta.get("fps", 30.0)

    # Try to get total frames (may not work for all codecs/containers)
    total_frames = None
    try:
        if hasattr(reader, "count_frames"):
            total_frames = reader.count_frames()
    except Exception:
        total_frames = None

    if total_frames is None or total_frames <= 0:
        # Fallback: iterate once to count, then reopen
        sys.stderr.write("Counting frames...\n")
        count = 0
        for _ in reader:
            count += 1
        reader.close()
        total_frames = count
        reader = imageio.get_reader(input_path)

    sys.stderr.write(f"Total frames detected: {total_frames}\n")
    sys.stderr.write(f"Using fps={fps}\n")
    sys.stderr.write(f"Writing ZIP archive: {output_zip}\n")

    # Open zip with STORED (WebP is already compressed)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        for idx, frame in enumerate(reader):
            t = idx / fps  # seconds
            ts_str = f"{t:0.6f}s"

            # Convert numpy array → PIL Image
            img = Image.fromarray(frame)

            # Encode to WebP in memory
            buf = io.BytesIO()
            if lossless:
                img.save(buf, format="WEBP", lossless=True, method=6)
                ext = "webp"
            else:
                img.save(buf, format="WEBP", quality=quality, method=6)
                ext = "webp"

            data = buf.getvalue()
            filename = f"{ts_str}.{ext}"

            # Write into zip
            zf.writestr(filename, data)

            progress_bar("Encoding & zipping", idx + 1, total_frames)

    reader.close()
    sys.stderr.write("\nDone.\n")
    sys.stderr.write(f"Created: {output_zip}\n")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Extract every frame from a video, encode each as WebP named by "
            "timestamp, and store them all in a ZIP archive."
        )
    )
    parser.add_argument("input", help="Input video file (e.g. clip.MOV)")
    parser.add_argument(
        "-o", "--output",
        help="Output zip file (default: <input_basename>_frames.zip)",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=90,
        help="WebP quality (0–100, higher = better quality, larger files; "
             "ignored if --lossless is set; default: 90)",
    )
    parser.add_argument(
        "--lossless",
        action="store_true",
        help="Use lossless WebP (larger files, but still usually smaller than PNG).",
    )

    args = parser.parse_args()

    if not os.path.isfile(args.input):
        sys.stderr.write(f"Input file not found: {args.input}\n")
        sys.exit(1)

    mov_frames_to_zip(
        input_path=args.input,
        output_zip=args.output,
        quality=args.quality,
        lossless=args.lossless,
    )


if __name__ == "__main__":
    main()
