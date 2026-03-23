#!/usr/bin/env python3
"""
freshest_photons.py

Query MAST for the newest image observations, apply a pragmatic deep-space
filter, and optionally download likely science FITS products.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import re
import threading
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


MAST_INVOKE_URL = "https://mast.stsci.edu/api/v0/invoke"
MAST_DOWNLOAD_URL = "https://mast.stsci.edu/api/v0.1/Download/file?uri={uri}"
CACHE_ROOT = Path("~/.cache/freshest_photons").expanduser()
API_CACHE_DIR = CACHE_ROOT / "api"
NOTES_FILE = CACHE_ROOT / "notes.json"
DEFAULT_COLLECTION = "JWST"
DEFAULT_SCAN = 300
DEFAULT_LIMIT = 10
DEFAULT_MIN_EXPTIME = 10.0

# Fast, blunt filter for obviously non-deep-space targets.
SOLAR_KEYWORDS = (
    "sun",
    "moon",
    "mercury",
    "venus",
    "mars",
    "jupiter",
    "saturn",
    "uranus",
    "neptune",
    "pluto",
    "ceres",
    "asteroid",
    "comet",
    "planet",
    "io",
    "europa",
    "ganymede",
    "callisto",
    "titan",
    "enceladus",
)

# Product subgroup preference for a quick "best image-ish FITS" pick.
PREFERRED_SUBGROUPS = ("I2D", "DRZ", "CAL", "FLT", "RATE", "RATEINT", "UNCAL")
GUI_COLLECTIONS = [
    "JWST",
    "HST",
    "TESS",
    "GALEX",
    "Kepler",
    "K2",
    "HLSP",
    "SWIFT",
    "EUCLID",
]
LATEST_CACHE: dict[str, dict[str, Any]] = {}
LATEST_CACHE_TS: dict[str, float] = {}
DOWNLOAD_PROGRESS: dict[str, dict[str, Any]] = {}
DOWNLOAD_PROGRESS_LOCK = threading.Lock()


def _cache_file_for_payload(payload: dict[str, Any]) -> Path:
    API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    key = hashlib.sha256(blob).hexdigest()
    return API_CACHE_DIR / f"{key}.json"


def _cache_ttl_for_payload(payload: dict[str, Any]) -> int:
    service = str(payload.get("service") or "")
    if service == "Mast.Caom.Products":
        return 60 * 60 * 24 * 30
    if service == "Mast.Caom.Filtered":
        return 60 * 5
    return 60 * 60 * 24


def load_notes() -> dict[str, str]:
    try:
        if NOTES_FILE.exists():
            data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def save_notes(notes: dict[str, str]) -> None:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = NOTES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(notes, indent=2), encoding="utf-8")
    tmp.replace(NOTES_FILE)


@dataclass
class Observation:
    obsid: int
    obs_collection: str
    target_name: str
    obs_title: str
    proposal_pi: str
    proposal_id: str
    t_exptime: float
    t_obs_release_mjd: float
    t_min_mjd: float
    t_max_mjd: float
    jpeg_url: str
    data_url: str

    @property
    def release_iso(self) -> str:
        return mjd_to_iso(self.t_obs_release_mjd)


def mjd_to_iso(mjd: float | int | None) -> str:
    if mjd in (None, ""):
        return ""
    dt = datetime(1858, 11, 17, tzinfo=timezone.utc) + timedelta(days=float(mjd))
    return dt.isoformat(timespec="seconds")


def mast_invoke(request_payload: dict[str, Any]) -> dict[str, Any]:
    refresh = os.environ.get("FRESHEST_PHOTONS_FORCE_REFRESH", "").lower() in ("1", "true", "yes")
    cache_file = _cache_file_for_payload(request_payload)
    ttl = _cache_ttl_for_payload(request_payload)
    if not refresh and cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age <= ttl:
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
    encoded = urllib.parse.urlencode(
        {"request": json.dumps(request_payload, separators=(",", ":"))}
    ).encode("utf-8")
    req = urllib.request.Request(
        MAST_INVOKE_URL,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    status = payload.get("status")
    if status != "COMPLETE":
        raise RuntimeError(f"MAST response status={status!r}")
    try:
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(cache_file)
    except Exception:
        pass
    return payload


def parse_collections(collection: str) -> list[str]:
    items = [c.strip() for c in str(collection).split(",") if c.strip()]
    if not items:
        return [DEFAULT_COLLECTION]
    if len(items) == 1 and items[0].upper() in ("ALL", "*"):
        return []
    return items


def query_latest_observations(
    *,
    collection: str,
    scan: int,
    page: int = 1,
) -> list[Observation]:
    collections = parse_collections(collection)
    filters: list[dict[str, Any]] = [
        {"paramName": "dataproduct_type", "values": ["image"]},
        {"paramName": "dataRights", "values": ["PUBLIC"]},
    ]
    if collections:
        filters.insert(0, {"paramName": "obs_collection", "values": collections})
    payload = {
        "service": "Mast.Caom.Filtered",
        "params": {
            "columns": (
                "obsid,obs_collection,target_name,obs_title,proposal_pi,proposal_id,"
                "t_exptime,t_obs_release,t_min,t_max,dataproduct_type,dataRights,jpegURL,dataURL"
            ),
            "filters": filters,
            "sort_by": [{"column": "t_obs_release", "descending": True}],
            "pagesize": int(scan),
            "page": max(1, int(page)),
        },
        "format": "json",
    }
    raw = mast_invoke(payload).get("data", [])
    rows: list[Observation] = []
    for row in raw:
        try:
            rows.append(
                Observation(
                    obsid=int(row.get("obsid")),
                    obs_collection=str(row.get("obs_collection") or ""),
                    target_name=str(row.get("target_name") or ""),
                    obs_title=str(row.get("obs_title") or ""),
                    proposal_pi=str(row.get("proposal_pi") or ""),
                    proposal_id=str(row.get("proposal_id") or ""),
                    t_exptime=float(row.get("t_exptime") or 0.0),
                    t_obs_release_mjd=float(row.get("t_obs_release") or 0.0),
                    t_min_mjd=float(row.get("t_min") or 0.0),
                    t_max_mjd=float(row.get("t_max") or 0.0),
                    jpeg_url=str(row.get("jpegURL") or ""),
                    data_url=str(row.get("dataURL") or ""),
                )
            )
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r.t_obs_release_mjd, reverse=True)
    return rows[:scan]


def looks_solar_system(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in SOLAR_KEYWORDS)


def deep_space_filter(
    observations: Iterable[Observation],
    *,
    min_exptime: float,
    include_solar_system: bool,
) -> list[Observation]:
    out: list[Observation] = []
    for obs in observations:
        if obs.t_exptime < min_exptime:
            continue
        if not include_solar_system:
            combined = f"{obs.target_name} {obs.obs_title}"
            if looks_solar_system(combined):
                continue
        out.append(obs)
    return out


def print_observation_table(observations: list[Observation], limit: int) -> None:
    print(
        "idx  release_utc              exptime_s  obsid      target                      title"
    )
    print("-" * 110)
    for i, obs in enumerate(observations[:limit], start=1):
        release = obs.release_iso.replace("+00:00", "Z")
        target = (obs.target_name or "").strip() or "UNKNOWN"
        title = (obs.obs_title or "").strip().replace("\n", " ")
        if len(target) > 26:
            target = target[:23] + "..."
        if len(title) > 42:
            title = title[:39] + "..."
        print(
            f"{i:>3}  {release:<24}  {obs.t_exptime:>9.1f}  {obs.obsid:<9}  "
            f"{target:<26}  {title}"
        )


def query_products(obsid: int) -> list[dict[str, Any]]:
    payload = {
        "service": "Mast.Caom.Products",
        "params": {"obsid": int(obsid)},
        "format": "json",
        "pagesize": 2000,
        "page": 1,
    }
    return mast_invoke(payload).get("data", [])


def score_product(product: dict[str, Any], subgroup_pref: tuple[str, ...]) -> int:
    filename = str(product.get("productFilename") or "")
    subgroup = str(product.get("productSubGroupDescription") or "").upper()
    ptype = str(product.get("productType") or "").upper()
    calib = int(product.get("calib_level") or 0)
    score = 0
    if filename.lower().endswith(".fits"):
        score += 30
    if ptype == "SCIENCE":
        score += 40
    if subgroup in subgroup_pref:
        score += 50 - subgroup_pref.index(subgroup)
    score += calib
    return score


def choose_products(
    products: list[dict[str, Any]],
    *,
    subgroup_pref: tuple[str, ...],
    max_files: int,
) -> list[dict[str, Any]]:
    fits = [
        p
        for p in products
        if str(p.get("productFilename") or "").lower().endswith(".fits")
        and str(p.get("dataURI") or "").startswith("mast:")
        and str(p.get("dataRights") or "PUBLIC").upper() == "PUBLIC"
    ]
    ranked = sorted(
        fits,
        key=lambda p: (
            score_product(p, subgroup_pref),
            int(p.get("size") or 0),
        ),
        reverse=True,
    )
    return ranked[:max_files]


def summarize_products(products: list[dict[str, Any]]) -> dict[str, Any]:
    total_size = sum(int(p.get("size") or 0) for p in products)
    fits = [
        p
        for p in products
        if str(p.get("productFilename") or "").lower().endswith(".fits")
    ]
    science = [p for p in products if str(p.get("productType") or "").upper() == "SCIENCE"]
    subgroup_counts: dict[str, int] = {}
    for p in products:
        subgroup = str(p.get("productSubGroupDescription") or "UNKNOWN")
        subgroup_counts[subgroup] = subgroup_counts.get(subgroup, 0) + 1
    largest = max((int(p.get("size") or 0) for p in products), default=0)
    grouped = sorted(
        [{"name": k, "count": v} for k, v in subgroup_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )
    return {
        "products_total": len(products),
        "fits_total": len(fits),
        "science_total": len(science),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "largest_file_mb": round(largest / (1024 * 1024), 2),
        "subgroup_counts": grouped,
    }


def download_product(
    product: dict[str, Any],
    output_dir: Path,
    *,
    dry_run: bool,
    progress_cb: Callable[[int, int], None] | None = None,
) -> Path:
    uri = str(product["dataURI"])
    filename = str(product.get("productFilename") or uri.split("/")[-1])
    expected_size = int(product.get("size") or 0)
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / filename
    url = MAST_DOWNLOAD_URL.format(uri=urllib.parse.quote(uri, safe=""))
    if out.exists():
        on_disk = out.stat().st_size
        if expected_size <= 0 or on_disk == expected_size:
            if progress_cb:
                progress_cb(on_disk, expected_size or on_disk)
            if dry_run:
                print(f"[dry-run] [cached] {out} ({on_disk} bytes)")
            return out
    if dry_run:
        if progress_cb:
            progress_cb(expected_size, expected_size)
        print(f"[dry-run] {url} -> {out}")
        return out
    with urllib.request.urlopen(url, timeout=180) as resp, out.open("wb") as f:
        total = int(resp.headers.get("Content-Length") or 0) or expected_size
        downloaded = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if progress_cb:
                progress_cb(downloaded, total)
    if progress_cb:
        final_size = out.stat().st_size if out.exists() else expected_size
        progress_cb(final_size, total or final_size)
    return out


def preview_url_for_obs(obs: Observation | None, products: list[dict[str, Any]]) -> str:
    if obs and obs.jpeg_url:
        if obs.jpeg_url.startswith("mast:"):
            return MAST_DOWNLOAD_URL.format(uri=urllib.parse.quote(obs.jpeg_url, safe=""))
        return obs.jpeg_url
    image_products = []
    for p in products:
        name = str(p.get("productFilename") or "").lower()
        if name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")) and str(
            p.get("dataURI") or ""
        ).startswith("mast:"):
            image_products.append(p)
    if image_products:
        best = max(image_products, key=lambda p: int(p.get("size") or 0))
        uri = str(best.get("dataURI"))
        return MAST_DOWNLOAD_URL.format(uri=urllib.parse.quote(uri, safe=""))
    return ""


def obs_to_dict(o: Observation) -> dict[str, Any]:
    preview = o.jpeg_url
    if preview.startswith("mast:"):
        preview = MAST_DOWNLOAD_URL.format(uri=urllib.parse.quote(preview, safe=""))
    if (
        not preview
        and o.data_url.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
    ):
        preview = o.data_url
        if preview.startswith("mast:"):
            preview = MAST_DOWNLOAD_URL.format(uri=urllib.parse.quote(preview, safe=""))
    return {
        "obsid": o.obsid,
        "obs_collection": o.obs_collection,
        "release_utc": o.release_iso,
        "t_exptime_s": o.t_exptime,
        "target_name": o.target_name,
        "obs_title": o.obs_title,
        "proposal_pi": o.proposal_pi,
        "proposal_id": o.proposal_id,
        "jpeg_url": o.jpeg_url,
        "data_url": o.data_url,
        "preview_url": preview,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Track and download the freshest deep-space-ish MAST images."
    )
    sub = p.add_subparsers(dest="cmd", required=False)

    latest = sub.add_parser("latest", help="List latest candidate observations (default).")
    latest.add_argument("--collection", default=DEFAULT_COLLECTION)
    latest.add_argument("--scan", type=int, default=DEFAULT_SCAN)
    latest.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    latest.add_argument("--min-exptime", type=float, default=DEFAULT_MIN_EXPTIME)
    latest.add_argument("--include-solar-system", action="store_true")
    latest.add_argument("--json", action="store_true")
    latest.add_argument(
        "--watch-seconds",
        type=int,
        default=0,
        help="Poll interval for tracker mode (0 disables watch).",
    )
    latest.add_argument(
        "--watch-iterations",
        type=int,
        default=0,
        help="Stop after N polls in watch mode (0 = run forever).",
    )

    dl = sub.add_parser("download", help="Download top FITS products from newest candidate.")
    dl.add_argument("--collection", default=DEFAULT_COLLECTION)
    dl.add_argument("--scan", type=int, default=DEFAULT_SCAN)
    dl.add_argument("--min-exptime", type=float, default=DEFAULT_MIN_EXPTIME)
    dl.add_argument("--include-solar-system", action="store_true")
    dl.add_argument("--obsid", type=int, default=None, help="Use a specific observation id.")
    dl.add_argument("--max-files", type=int, default=1)
    dl.add_argument(
        "--subgroups",
        default=",".join(PREFERRED_SUBGROUPS),
        help="Preferred product subgroup order, comma-separated (default: I2D,DRZ,CAL,FLT,RATE,RATEINT,UNCAL).",
    )
    dl.add_argument("--output-dir", default="./freshest_photons")
    dl.add_argument("--dry-run", action="store_true")

    gui = sub.add_parser("gui", help="Launch local browser UI with all controls.")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--no-browser", action="store_true")

    return p.parse_args()


def list_cmd(args: argparse.Namespace) -> int:
    def fetch() -> tuple[list[Observation], list[Observation]]:
        rows = query_latest_observations(collection=args.collection, scan=args.scan)
        filtered = deep_space_filter(
            rows,
            min_exptime=args.min_exptime,
            include_solar_system=args.include_solar_system,
        )
        return rows, filtered

    if args.watch_seconds <= 0:
        rows, filtered = fetch()
        if not filtered:
            print("No matching observations found.")
            return 2
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "obsid": o.obsid,
                            "release_utc": o.release_iso,
                            "t_exptime_s": o.t_exptime,
                            "target_name": o.target_name,
                            "obs_title": o.obs_title,
                            "proposal_pi": o.proposal_pi,
                            "proposal_id": o.proposal_id,
                        }
                        for o in filtered[: args.limit]
                    ],
                    indent=2,
                )
            )
        else:
            print_observation_table(filtered, args.limit)
            print(f"\nScanned {len(rows)} rows, {len(filtered)} passed filters.")
        return 0

    if args.json:
        print("--json is not supported in watch mode.", file=sys.stderr)
        return 2

    top_obsid: int | None = None
    polls = 0
    while True:
        polls += 1
        rows, filtered = fetch()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        if not filtered:
            print(f"[{now}] no matching observations.")
        else:
            head = filtered[0]
            changed = head.obsid != top_obsid
            marker = "NEW" if changed else "same"
            print(f"\n[{now}] top={head.obsid} release={head.release_iso} ({marker})")
            if changed:
                top_obsid = head.obsid
                print_observation_table(filtered, args.limit)
        if args.watch_iterations > 0 and polls >= args.watch_iterations:
            return 0
        time.sleep(max(1, args.watch_seconds))


def download_cmd(args: argparse.Namespace) -> int:
    if args.obsid is None:
        rows = query_latest_observations(collection=args.collection, scan=args.scan)
        filtered = deep_space_filter(
            rows,
            min_exptime=args.min_exptime,
            include_solar_system=args.include_solar_system,
        )
        if not filtered:
            print("No matching observations found for download.")
            return 2
        obsid = filtered[0].obsid
    else:
        obsid = args.obsid

    products = query_products(obsid)
    subgroup_pref = tuple(s.strip().upper() for s in args.subgroups.split(",") if s.strip())
    chosen = choose_products(products, subgroup_pref=subgroup_pref, max_files=args.max_files)
    if not chosen:
        print(f"No public FITS products found for obsid={obsid}.")
        return 3

    print(f"obsid={obsid} products={len(products)} selected={len(chosen)}")
    out_dir = Path(args.output_dir).expanduser()
    for p in chosen:
        subgroup = str(p.get("productSubGroupDescription") or "")
        name = str(p.get("productFilename") or "")
        size_mb = int(p.get("size") or 0) / (1024 * 1024)
        print(f"  - {name} subgroup={subgroup} size_mb={size_mb:.1f}")
        out = download_product(p, out_dir, dry_run=args.dry_run)
        print(f"    -> {out}")
    return 0


GUI_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Freshest Photons</title>
  <style>
    :root {
      --bg:#0b1020; --ink:#eaf0ff; --muted:#94a3c7; --line:#243355;
      --panel:#101a32; --accent:#39c0ff; --ok:#7ef6bf; --err:#ff9b9b;
    }
    body{margin:0;color:var(--ink);font:14px/1.45 "JetBrains Mono","Fira Code",ui-monospace,monospace;background:radial-gradient(circle at 5% 0%,#24395f 0,#0b1020 45%) fixed;}
    .wrap{max-width:1280px;margin:16px auto 70px;padding:0 12px;}
    .box{background:linear-gradient(180deg,#121d38,#0f1830);border:1px solid var(--line);border-radius:14px;padding:12px;margin-bottom:12px;box-shadow:0 8px 28px rgba(0,0,0,.28);}
    h1{margin:0 0 8px;font-size:22px;letter-spacing:.4px}
    .muted{color:var(--muted)} .ok{color:var(--ok)} .err{color:var(--err)}
    .controls{display:grid;grid-template-columns:repeat(8,minmax(120px,1fr));gap:8px}
    label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
    input,select,button{width:100%;box-sizing:border-box;background:#0a1326;color:var(--ink);border:1px solid #31466f;border-radius:9px;padding:8px}
    button{cursor:pointer;background:linear-gradient(180deg,#194a72,#163f61)}
    button.alt{background:#13233e}
    .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .stream{display:grid;grid-template-columns:repeat(auto-fill,minmax(295px,1fr));gap:10px}
    .progress-wrap{margin:8px 0 2px}
    .progress-track{height:8px;background:#0a1326;border:1px solid #31466f;border-radius:999px;overflow:hidden}
    .progress-bar{height:100%;width:0;background:linear-gradient(90deg,#39c0ff,#7ef6bf);transition:width .18s ease}
    .progress-bar.indeterminate{width:35%;animation:streamSlide 1.1s linear infinite}
    @keyframes streamSlide{0%{transform:translateX(-120%)}100%{transform:translateX(320%)}}
    .stream-foot{padding:12px;text-align:center;color:var(--muted)}
    .stream-foot button{max-width:220px;margin:0 auto;display:block}
    .card{border:1px solid #263a60;background:#0e1730;border-radius:12px;overflow:hidden}
    .card .img{height:170px;background:linear-gradient(135deg,#112546,#0c1630);display:flex;align-items:center;justify-content:center;color:#7f97c5;font-size:12px}
    .card img{width:100%;height:100%;object-fit:cover;display:block}
    .meta{padding:9px}
    .meta .title{font-size:12px;max-height:35px;overflow:hidden}
    .tiny{font-size:11px}
    .modal{position:fixed;inset:0;background:rgba(7,12,24,.78);display:none;z-index:40;backdrop-filter:blur(2px)}
    .modal.open{display:block}
    .modal-inner{max-width:1180px;margin:20px auto;background:#0f1730;border:1px solid #2b4068;border-radius:14px;overflow:hidden;box-shadow:0 20px 70px rgba(0,0,0,.5)}
    .modal-head{display:flex;justify-content:space-between;align-items:center;padding:10px 12px;border-bottom:1px solid #20355c}
    .modal-grid{display:grid;grid-template-columns:2fr 1fr;gap:0}
    .modal-image{min-height:420px;background:#091329;display:flex;align-items:center;justify-content:center}
    .modal-image img{max-width:100%;max-height:72vh;display:block}
    .modal-side{padding:12px;border-left:1px solid #20355c}
    .stat-grid{display:grid;grid-template-columns:repeat(2,minmax(120px,1fr));gap:8px}
    .stat{background:#0b1429;border:1px solid #243b63;border-radius:10px;padding:8px}
    .files{max-height:220px;overflow:auto;border:1px solid #243b63;border-radius:8px;padding:8px;background:#0a1224}
    .file-row{padding:6px;border:1px solid #243b63;border-radius:7px;margin-bottom:6px;cursor:pointer}
    .file-row:hover{background:#0f1d39}
    .file-row.active{outline:2px solid #39c0ff}
    .detail-box{margin-top:8px;border:1px solid #243b63;border-radius:8px;padding:8px;background:#0a1224}
    .detail-box textarea{width:100%;min-height:90px;box-sizing:border-box;background:#0b1326;color:var(--ink);border:1px solid #31466f;border-radius:8px;padding:8px}
    @media (max-width:980px){.modal-grid{grid-template-columns:1fr}.modal-side{border-left:none;border-top:1px solid #20355c}}
    pre{white-space:pre-wrap}
    @media (max-width:980px){.controls{grid-template-columns:repeat(2,minmax(140px,1fr));}}
  </style>
</head>
<body>
<div class="wrap">
  <div class="box">
    <h1>Freshest Photons</h1>
    <div class="muted">Live stream of recent archive observations. Select a collection, tune filters, click any card to target downloads.</div>
  </div>

  <div class="box">
    <div class="controls">
      <div><label>Collection</label><select id="collection"></select></div>
      <div><label>Scan Rows</label><input id="scan" type="number" value="200"></div>
      <div><label>Cards</label><input id="limit" type="number" value="18"></div>
      <div><label>Min Exptime (s)</label><input id="minExptime" type="number" step="0.1" value="10"></div>
      <div><label>Include Solar System</label><div><input id="includeSolar" type="checkbox"></div></div>
      <div><label>Selected Obsid</label><input id="obsid"></div>
      <div><label>Output Directory</label><input id="outputDir" value="./freshest_photons"></div>
    </div>
    <div class="row" style="margin-top:8px">
      <button id="refreshBtn">Refresh Stream</button>
      <span id="status" class="muted"></span>
    </div>
  </div>

  <div class="box">
    <div class="progress-wrap">
      <div class="tiny muted" id="progressText">idle</div>
      <div class="progress-track"><div id="progressBar" class="progress-bar"></div></div>
    </div>
    <div id="stream" class="stream"></div>
    <div id="streamFoot" class="stream-foot tiny muted">
      <div class="row" style="justify-content:center">
        <button id="prevPageBtn" class="alt" style="max-width:180px">Previous Page</button>
        <button id="nextPageBtn" class="alt" style="max-width:180px">Next Page</button>
      </div>
      <div id="streamHint" style="margin-top:6px">Page 1</div>
    </div>
  </div>

  <div class="box">
    <div class="controls">
      <div><label>Max Files</label><input id="maxFiles" type="number" value="1"></div>
      <div><label>Subgroups</label><input id="subgroups" value="I2D,DRZ,CAL,FLT,RATE,RATEINT,UNCAL"></div>
      <div><label>Dry Run</label><div><input id="dryRun" type="checkbox" checked></div></div>
    </div>
    <div class="row" style="margin-top:8px">
      <button id="productsBtn" class="alt">Preview Products</button>
      <button id="downloadBtn">Download Selected</button>
    </div>
    <pre id="log" style="margin-top:10px"></pre>
  </div>
</div>
<div id="obsModal" class="modal">
  <div class="modal-inner">
    <div class="modal-head">
      <div>
        <strong id="modalTitle">Observation</strong>
        <div id="modalSub" class="tiny muted"></div>
      </div>
      <button id="modalClose" style="max-width:120px">Close</button>
    </div>
    <div class="modal-grid">
      <div class="modal-image">
        <img id="modalImg" alt="full resolution preview">
      </div>
      <div class="modal-side">
        <div id="modalStats" class="stat-grid"></div>
        <h4 style="margin:12px 0 6px">Top Files</h4>
        <div id="modalFiles" class="files tiny"></div>
        <div id="fileDetail" class="detail-box tiny">
          <div class="muted">Click a file to inspect details.</div>
        </div>
      </div>
    </div>
  </div>
</div>
<script>
const $ = (id) => document.getElementById(id);
let cardData = [];
let inFlight = false;
let modalRequestSeq = 0;
let modalTopFiles = [];
let currentNoteFile = null;
let currentPage = 1;
let hasMore = true;
let loadingPage = false;
let downloadPollTimer = null;

function setStatus(msg, cls="muted"){const el=$("status");el.className=cls;el.textContent=msg;}
function writeLog(v){$("log").textContent = (typeof v==="string") ? v : JSON.stringify(v,null,2);}
function fmtMB(v){return `${Number(v||0).toFixed(1)} MB`;}
function fmtBytesMB(v){return `${(Number(v||0)/(1024*1024)).toFixed(1)} MB`;}
function queryString(){
  return new URLSearchParams({
    collection: $("collection").value || "JWST",
    scan: $("scan").value || "200",
    limit: $("limit").value || "18",
    min_exptime: $("minExptime").value || "10",
    include_solar_system: $("includeSolar").checked ? "1" : "0",
  }).toString();
}

function setProgress(text, pct=null){
  $("progressText").textContent = text || "idle";
  const bar = $("progressBar");
  if(pct === null){
    bar.classList.add("indeterminate");
    bar.style.width = "35%";
    return;
  }
  const clamped = Math.max(0, Math.min(100, Number(pct)));
  bar.classList.remove("indeterminate");
  bar.style.transform = "";
  bar.style.width = `${clamped}%`;
}

function updatePageControls(){
  $("prevPageBtn").disabled = loadingPage || currentPage <= 1;
  $("nextPageBtn").disabled = loadingPage || !hasMore;
  $("streamHint").textContent = hasMore ? `Page ${currentPage}` : `Page ${currentPage} (end)`;
}

async function api(path, opts={}){
  function requestTimeoutMs(p){
    if(p.startsWith("/api/latest")) return 180000;
    if(p.startsWith("/api/obs_detail")) return 120000;
    if(p.startsWith("/api/preview")) return 120000;
    return 30000;
  }
  for(let attempt=1; attempt<=2; attempt++){
    try{
      const ctrl = new AbortController();
      const timeoutMs = requestTimeoutMs(path);
      const tmo = setTimeout(() => ctrl.abort(), timeoutMs);
      const r = await fetch(path, {...opts, signal: ctrl.signal});
      clearTimeout(tmo);
      const t = await r.text();
      let data = {};
      try { data = t ? JSON.parse(t) : {}; } catch { throw new Error(`Invalid JSON from ${path}: ${t.slice(0,140)}`); }
      if(!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
      return data;
    }catch(err){
      if(attempt === 2){
        const msg = (err && err.name === "AbortError")
          ? `Request timed out for ${path}`
          : `Request failed for ${path}: ${err.message}`;
        throw new Error(msg);
      }
      await new Promise(res => setTimeout(res, 500));
    }
  }
}

function selectObsid(obsid){
  $("obsid").value = String(obsid||"");
  document.querySelectorAll(".card").forEach(c => c.style.outline = "");
  const el = document.getElementById("card-"+obsid);
  if(el) el.style.outline = "2px solid #39c0ff";
}

function openModal(){ $("obsModal").classList.add("open"); }
function closeModal(){ $("obsModal").classList.remove("open"); }

function fileKey(obsid, file){ return `${obsid}::${file.productFilename || ""}`; }

function renderTopFiles(obsid){
  const html = modalTopFiles.map((f, idx) => {
    const key = fileKey(obsid, f);
    return `<div class="file-row${idx===0 ? " active" : ""}" data-key="${key}" data-idx="${idx}">
      <div>${f.productFilename || "unknown file"}</div>
      <div class="tiny muted">${f.productSubGroupDescription || "-"} | ${fmtMB(f.size_mb)}</div>
    </div>`;
  }).join("");
  $("modalFiles").innerHTML = html || `<div class="muted">No files.</div>`;
  document.querySelectorAll("#modalFiles .file-row").forEach(el => {
    el.onclick = () => showFileDetails(obsid, Number(el.dataset.idx || "0"));
  });
}

async function showFileDetails(obsid, idx){
  const f = modalTopFiles[idx];
  if(!f){ return; }
  document.querySelectorAll("#modalFiles .file-row").forEach(r => r.classList.remove("active"));
  const row = document.querySelector(`#modalFiles .file-row[data-idx="${idx}"]`);
  if(row){ row.classList.add("active"); }
  currentNoteFile = f;
  const key = encodeURIComponent(fileKey(obsid, f));
  let note = "";
  try{
    const n = await api(`/api/note?key=${key}`);
    note = n.note || "";
  }catch{}
  const link = f.download_url ? `<a href="${f.download_url}" target="_blank" rel="noopener">Open/download</a>` : "";
  $("fileDetail").innerHTML = `
    <div><strong>${f.productFilename || "unknown"}</strong></div>
    <div class="tiny muted" style="margin:4px 0">
      subgroup: ${f.productSubGroupDescription || "-"} | type: ${f.productType || "-"} | calib: ${f.calib_level ?? "-"} | size: ${fmtMB(f.size_mb)}
    </div>
    <div class="tiny">${f.description || ""}</div>
    <div style="margin:6px 0">${link}</div>
    <label class="tiny muted">Notes</label>
    <textarea id="fileNoteBox" placeholder="Add your notes/comments for this file...">${note}</textarea>
    <div class="row" style="margin-top:6px"><button id="saveNoteBtn">Save Note</button></div>
  `;
  $("saveNoteBtn").onclick = async () => {
    const noteText = $("fileNoteBox").value || "";
    await api("/api/note", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({ key: fileKey(obsid, f), note: noteText })
    });
    writeLog("Note saved.");
  };
}

async function openObservationModal(obsid){
  const reqId = ++modalRequestSeq;
  const obs = cardData.find(o => String(o.obsid) === String(obsid));
  selectObsid(obsid);
  $("modalTitle").textContent = obs ? (obs.obs_title || `obsid ${obsid}`) : `obsid ${obsid}`;
  $("modalSub").textContent = obs ? `${(obs.release_utc||"").replace("+00:00","Z")} | ${obs.obs_collection} | target ${obs.target_name || "UNKNOWN"}` : "";
  const clickedPreview = (obs && obs.preview_url) ? obs.preview_url : "";
  $("modalImg").src = clickedPreview;
  $("modalStats").innerHTML = `<div class="stat tiny muted">Loading statistics...</div>`;
  $("modalFiles").innerHTML = `<div class="muted">Loading files...</div>`;
  $("fileDetail").innerHTML = `<div class="muted">Loading details...</div>`;
  openModal();
  const details = await api("/api/obs_detail?obsid=" + encodeURIComponent(String(obsid)));
  if(reqId !== modalRequestSeq){ return; }
  // Keep the exact clicked thumbnail image when available to avoid visual mismatches.
  if(!clickedPreview && details.preview_url){ $("modalImg").src = details.preview_url; }
  const stats = details.stats || {};
  $("modalStats").innerHTML = [
    ["Products", stats.products_total],
    ["FITS", stats.fits_total],
    ["Science", stats.science_total],
    ["Total Size", fmtMB(stats.total_size_mb)],
    ["Largest", fmtMB(stats.largest_file_mb)],
    ["Subgroups", (stats.subgroup_counts||[]).length]
  ].map(([k,v]) => `<div class="stat"><div class="tiny muted">${k}</div><div>${v}</div></div>`).join("");
  modalTopFiles = details.top_files || [];
  renderTopFiles(obsid);
  if(modalTopFiles.length){ showFileDetails(obsid, 0); }
}

function cardHtml(o){
  const rel = (o.release_utc||"").replace("+00:00","Z");
  const title = o.obs_title || "";
  const preview = `<div class="thumb-slot tiny muted">loading preview...<br><span class="tiny">${o.obs_collection}</span></div>`;
  return `<article class="card" id="card-${o.obsid}" data-obsid="${o.obsid}">
    <div class="img">${preview}</div>
    <div class="meta">
      <div class="tiny muted">${rel} | ${o.obs_collection} | ${Number(o.t_exptime_s).toFixed(1)}s</div>
      <div class="title">${title}</div>
      <div class="tiny">target: ${o.target_name || "UNKNOWN"} | obsid: ${o.obsid}</div>
    </div>
  </article>`;
}

function applyCardPreview(obs, url){
  const card = document.getElementById(`card-${obs.obsid}`);
  if(!card){ return; }
  const imgWrap = card.querySelector(".img");
  if(!url){
    imgWrap.innerHTML = `<div class="tiny muted">no preview image<br>${obs.obs_collection}</div>`;
    return;
  }
  const im = new Image();
  // Important: append before setting src so browser actually starts the fetch.
  // Off-DOM lazy images can stall until some unrelated interaction happens.
  im.loading = "eager";
  im.alt = `preview ${obs.obsid}`;
  im.onload = () => {
    im.style.opacity = "1";
  };
  im.onerror = () => {
    imgWrap.innerHTML = `<div class="tiny muted">preview unavailable</div>`;
  };
  im.style.opacity = "0";
  im.style.transition = "opacity .18s ease";
  imgWrap.innerHTML = "";
  imgWrap.appendChild(im);
  im.src = url;
}

async function hydrateThumbnails(observations){
  const pending = observations.filter(o => !o.preview_url);
  observations.filter(o => !!o.preview_url).forEach(o => applyCardPreview(o, o.preview_url));
  const chunkSize = 8;
  for(let i=0; i<pending.length; i += chunkSize){
    const chunk = pending.slice(i, i + chunkSize);
    const ids = chunk.map(o => String(o.obsid)).join(",");
    try{
      const data = await api(`/api/previews_batch?obsids=${encodeURIComponent(ids)}`);
      const previews = data.previews || {};
      chunk.forEach(o => applyCardPreview(o, previews[String(o.obsid)] || ""));
    }catch{
      chunk.forEach(o => applyCardPreview(o, ""));
    }
  }
}

function renderPage(observations){
  const stream = $("stream");
  const html = observations.map(cardHtml).join("");
  stream.innerHTML = html;
  cardData = [];
  observations.forEach(obs => {
    cardData.push(obs);
    const el = document.getElementById(`card-${obs.obsid}`);
    if(el){
      el.onclick = () => openObservationModal(el.dataset.obsid).catch(e => setStatus(e.message, "err"));
    }
  });
  hydrateThumbnails(observations).catch(() => {});
}

async function loadPage(page){
  if(loadingPage){ return; }
  if(page < 1){ return; }
  if(page > 1 && !hasMore && page > currentPage){ return; }
  loadingPage = true;
  updatePageControls();
  setProgress(`Fetching stream page ${page}...`, null);
  setStatus(`Loading page ${page}...`);
  try{
    const data = await api("/api/latest?" + queryString() + `&page=${page}`);
    const observations = data.observations || [];
    renderPage(observations);
    currentPage = page;
    if(observations[0]){ selectObsid(observations[0].obsid); }
    const suffix = data.cached ? " (cached)" : "";
    setStatus(`Page ${page} loaded: ${observations.length} cards${suffix}`, "ok");
    setProgress(`Stream page ${page} loaded (${observations.length} cards)`, 100);
    if(data.warning){ writeLog(data.warning); }
    hasMore = Boolean(data.has_more) && observations.length > 0;
    updatePageControls();
  }catch(err){
    try{
      await api("/api/health");
      setStatus(err.message, "err");
    }catch{
      setStatus(`${err.message} (backend not reachable on ${location.origin})`, "err");
    }
  }finally{
    loadingPage = false;
    updatePageControls();
  }
}

async function resetStream(){
  if(inFlight){ return; }
  inFlight = true;
  loadingPage = true;
  updatePageControls();
  setProgress(`Refreshing page ${currentPage}...`, null);
  setStatus(`Refreshing page ${currentPage}...`);
  try{
    const data = await api("/api/latest?" + queryString() + `&page=${currentPage}`);
    const observations = data.observations || [];
    renderPage(observations);
    hasMore = Boolean(data.has_more) && observations.length > 0;
    if(observations[0]){ selectObsid(observations[0].obsid); }
    const suffix = data.cached ? " (cached)" : "";
    setStatus(`Scanned ${data.scanned}; matched ${data.matched}; page ${currentPage} showing ${observations.length}${suffix}`, "ok");
    setProgress(`Page ${currentPage} loaded (${observations.length} cards)`, 100);
    updatePageControls();
  }finally{
    inFlight = false;
    loadingPage = false;
    updatePageControls();
  }
}

async function previewProducts(){
  const obsid = $("obsid").value.trim();
  if(!obsid) return writeLog("Select a card first.");
  const qs = new URLSearchParams({obsid,max_files:$("maxFiles").value||"5",subgroups:$("subgroups").value});
  writeLog(await api("/api/products?"+qs.toString()));
}

async function downloadSelected(){
  if(downloadPollTimer){ clearInterval(downloadPollTimer); downloadPollTimer = null; }
  const downloadId = `dl-${Date.now()}-${Math.random().toString(16).slice(2,8)}`;
  const payload = {
    obsid: Number($("obsid").value),
    max_files: Number($("maxFiles").value||"1"),
    subgroups: $("subgroups").value,
    output_dir: $("outputDir").value,
    dry_run: $("dryRun").checked,
    download_id: downloadId
  };
  const poll = async () => {
    try{
      const p = await api(`/api/download_progress?download_id=${encodeURIComponent(downloadId)}`);
      const total = Number(p.total_bytes || 0);
      const done = Number(p.downloaded_bytes || 0);
      const pct = total > 0 ? Math.round((done / total) * 100) : null;
      if(p.state === "running"){
        const label = p.current_file
          ? `Downloading ${p.current_file} (${fmtBytesMB(done)}/${fmtBytesMB(total)})`
          : `Downloading (${fmtBytesMB(done)}/${fmtBytesMB(total)})`;
        setProgress(label, pct);
      }else if(p.state === "done"){
        setProgress(`Download complete (${fmtBytesMB(done)})`, 100);
      }else if(p.state === "error"){
        setProgress(`Download failed: ${p.error || "unknown error"}`, 100);
      }
    }catch{}
  };
  setProgress("Preparing download...", null);
  downloadPollTimer = setInterval(poll, 450);
  await poll();
  try{
    writeLog(await api("/api/download",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}));
    await poll();
  }finally{
    if(downloadPollTimer){ clearInterval(downloadPollTimer); downloadPollTimer = null; }
  }
}

async function init(){
  const cols = await api("/api/collections");
  $("collection").innerHTML = cols.collections.map(c => `<option value="${c}">${c}</option>`).join("");
  $("collection").value = "JWST";
  updatePageControls();
  await resetStream();
}

$("refreshBtn").onclick = () => resetStream().catch(e => setStatus(e.message, "err"));
$("productsBtn").onclick = () => previewProducts().catch(e => writeLog(e.message));
$("downloadBtn").onclick = () => downloadSelected().catch(e => writeLog(e.message));
$("nextPageBtn").onclick = () => loadPage(currentPage + 1).catch(e => setStatus(e.message, "err"));
$("prevPageBtn").onclick = () => loadPage(currentPage - 1).catch(e => setStatus(e.message, "err"));
$("modalClose").onclick = closeModal;
$("obsModal").onclick = (e) => { if(e.target.id === "obsModal"){ closeModal(); } };
["collection","scan","limit","minExptime","includeSolar"].forEach(id => {
  const el = $(id);
  if(!el){ return; }
  const evt = (id === "includeSolar" || el.tagName === "SELECT") ? "change" : "input";
  el.addEventListener(evt, () => {
    currentPage = 1;
    updatePageControls();
  });
});
init().catch(e => setStatus(e.message, "err"));
</script>
</body>
</html>
"""


def gui_cmd(args: argparse.Namespace) -> int:
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "freshest-photons/1.0"

        def _send_json(self, obj: dict[str, Any], status: int = 200) -> None:
            data = json.dumps(obj, indent=2).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                # Client disconnected while we were writing.
                return

        def _send_html(self, html: str) -> None:
            data = html.encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def _query_bool(self, params: dict[str, list[str]], key: str, default: bool = False) -> bool:
            val = (params.get(key, ["1" if default else "0"])[0] or "").lower()
            return val in ("1", "true", "yes", "on")

        def log_message(self, fmt: str, *args2: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._send_html(GUI_HTML)
                return
            if parsed.path == "/api/collections":
                self._send_json({"collections": ["ALL", *GUI_COLLECTIONS]})
                return
            if parsed.path == "/api/health":
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/download_progress":
                try:
                    q = urllib.parse.parse_qs(parsed.query)
                    download_id = str(q.get("download_id", [""])[0]).strip()
                    if not download_id:
                        self._send_json({"error": "download_id is required"}, status=400)
                        return
                    with DOWNLOAD_PROGRESS_LOCK:
                        progress = DOWNLOAD_PROGRESS.get(download_id)
                    if not progress:
                        self._send_json(
                            {
                                "download_id": download_id,
                                "state": "unknown",
                                "downloaded_bytes": 0,
                                "total_bytes": 0,
                            }
                        )
                        return
                    self._send_json(progress)
                except Exception as e:
                    self._send_json({"error": str(e)}, status=500)
                return
            if parsed.path == "/api/latest":
                q = urllib.parse.parse_qs(parsed.query)
                collection = q.get("collection", [DEFAULT_COLLECTION])[0]
                scan = int(q.get("scan", [str(DEFAULT_SCAN)])[0])
                limit = int(q.get("limit", [str(DEFAULT_LIMIT)])[0])
                page = int(q.get("page", ["1"])[0])
                min_exptime = float(q.get("min_exptime", [str(DEFAULT_MIN_EXPTIME)])[0])
                include_solar = self._query_bool(q, "include_solar_system", False)
                cache_key = json.dumps(
                    {
                        "collection": collection,
                        "scan": scan,
                        "limit": limit,
                        "page": page,
                        "min_exptime": min_exptime,
                        "include_solar": include_solar,
                    },
                    sort_keys=True,
                )
                now = time.time()
                if cache_key in LATEST_CACHE and now - LATEST_CACHE_TS.get(cache_key, 0) < 45:
                    self._send_json({**LATEST_CACHE[cache_key], "cached": True})
                    return
                try:
                    resolved_collection = collection
                    resolved_page = 1
                    rows = query_latest_observations(
                        collection=resolved_collection,
                        scan=scan,
                        page=resolved_page,
                    )
                    filtered = deep_space_filter(
                        rows,
                        min_exptime=min_exptime,
                        include_solar_system=include_solar,
                    )
                    start = max(0, (max(1, page) - 1) * max(1, limit))
                    end = start + max(1, limit)
                    subset = filtered[start:end]
                    payload = {
                        "page": page,
                        "has_more": end < len(filtered),
                        "resolved_collection": resolved_collection,
                        "resolved_page": resolved_page,
                        "scanned": len(rows),
                        "matched": len(filtered),
                        "observations": [obs_to_dict(o) for o in subset],
                        "cached": False,
                    }
                    LATEST_CACHE[cache_key] = payload
                    LATEST_CACHE_TS[cache_key] = now
                    self._send_json(payload)
                except Exception as e:
                    if cache_key in LATEST_CACHE:
                        self._send_json(
                            {
                                **LATEST_CACHE[cache_key],
                                "cached": True,
                                "warning": f"live refresh failed: {e}",
                            }
                        )
                    else:
                        self._send_json({"error": str(e)}, status=500)
                return
            if parsed.path == "/api/preview":
                try:
                    q = urllib.parse.parse_qs(parsed.query)
                    obsid = int(q.get("obsid", ["0"])[0])
                    products = query_products(obsid)
                    self._send_json({"obsid": obsid, "preview_url": preview_url_for_obs(None, products)})
                except Exception as e:
                    self._send_json({"error": str(e)}, status=500)
                return
            if parsed.path == "/api/previews_batch":
                try:
                    q = urllib.parse.parse_qs(parsed.query)
                    raw = q.get("obsids", [""])[0]
                    ids = []
                    for part in raw.split(","):
                        part = part.strip()
                        if not part:
                            continue
                        try:
                            ids.append(int(part))
                        except ValueError:
                            continue
                    ids = ids[:60]
                    previews: dict[str, str] = {}
                    for oid in ids:
                        try:
                            products = query_products(oid)
                            previews[str(oid)] = preview_url_for_obs(None, products)
                        except Exception:
                            previews[str(oid)] = ""
                    self._send_json({"previews": previews})
                except Exception as e:
                    self._send_json({"error": str(e)}, status=500)
                return
            if parsed.path == "/api/products":
                try:
                    q = urllib.parse.parse_qs(parsed.query)
                    obsid = int(q.get("obsid", ["0"])[0])
                    max_files = int(q.get("max_files", ["5"])[0])
                    subgroups = tuple(
                        s.strip().upper()
                        for s in q.get("subgroups", [",".join(PREFERRED_SUBGROUPS)])[0].split(",")
                        if s.strip()
                    )
                    products = query_products(obsid)
                    chosen = choose_products(products, subgroup_pref=subgroups, max_files=max_files)
                    self._send_json(
                        {
                            "obsid": obsid,
                            "products_total": len(products),
                            "selected": [
                                {
                                    "productFilename": p.get("productFilename"),
                                    "productSubGroupDescription": p.get("productSubGroupDescription"),
                                    "size_mb": round(int(p.get("size") or 0) / (1024 * 1024), 2),
                                    "dataURI": p.get("dataURI"),
                                }
                                for p in chosen
                            ],
                        }
                    )
                except Exception as e:
                    self._send_json({"error": str(e)}, status=500)
                return
            if parsed.path == "/api/obs_detail":
                try:
                    q = urllib.parse.parse_qs(parsed.query)
                    obsid = int(q.get("obsid", ["0"])[0])
                    products = query_products(obsid)
                    stats = summarize_products(products)
                    top_files = sorted(
                        products,
                        key=lambda p: int(p.get("size") or 0),
                        reverse=True,
                    )[:12]
                    self._send_json(
                        {
                            "obsid": obsid,
                            "preview_url": preview_url_for_obs(None, products),
                            "stats": stats,
                            "top_files": [
                                {
                                    "productFilename": p.get("productFilename"),
                                    "productSubGroupDescription": p.get("productSubGroupDescription"),
                                    "size_mb": round(int(p.get("size") or 0) / (1024 * 1024), 2),
                                    "productType": p.get("productType"),
                                    "calib_level": p.get("calib_level"),
                                    "description": p.get("description"),
                                    "dataURI": p.get("dataURI"),
                                    "download_url": (
                                        MAST_DOWNLOAD_URL.format(
                                            uri=urllib.parse.quote(str(p.get("dataURI") or ""), safe="")
                                        )
                                        if str(p.get("dataURI") or "").startswith("mast:")
                                        else ""
                                    ),
                                }
                                for p in top_files
                            ],
                        }
                    )
                except Exception as e:
                    self._send_json({"error": str(e)}, status=500)
                return
            if parsed.path == "/api/note":
                try:
                    q = urllib.parse.parse_qs(parsed.query)
                    key = str(q.get("key", [""])[0])
                    notes = load_notes()
                    self._send_json({"key": key, "note": notes.get(key, "")})
                except Exception as e:
                    self._send_json({"error": str(e)}, status=500)
                return
            self._send_json({"error": "not found"}, status=404)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path not in ("/api/download", "/api/note"):
                self._send_json({"error": "not found"}, status=404)
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n) if n > 0 else b"{}"
                payload = json.loads(body.decode("utf-8"))
                if parsed.path == "/api/note":
                    key = str(payload.get("key", "")).strip()
                    note = str(payload.get("note", ""))
                    if not key:
                        self._send_json({"error": "key is required"}, status=400)
                        return
                    notes = load_notes()
                    if note.strip():
                        notes[key] = note
                    else:
                        notes.pop(key, None)
                    save_notes(notes)
                    self._send_json({"ok": True, "key": key})
                    return
                obsid = int(payload.get("obsid", 0))
                max_files = int(payload.get("max_files", 1))
                out_dir = Path(str(payload.get("output_dir", "./freshest_photons"))).expanduser()
                dry_run = bool(payload.get("dry_run", True))
                download_id = str(payload.get("download_id", "")).strip() or f"dl-{int(time.time() * 1000)}"
                subgroups = tuple(
                    s.strip().upper()
                    for s in str(payload.get("subgroups", ",".join(PREFERRED_SUBGROUPS))).split(",")
                    if s.strip()
                )
                products = query_products(obsid)
                chosen = choose_products(products, subgroup_pref=subgroups, max_files=max_files)
                if not chosen:
                    self._send_json({"error": f"No public FITS products found for obsid={obsid}."}, status=404)
                    return
                total_bytes = sum(int(p.get("size") or 0) for p in chosen)
                with DOWNLOAD_PROGRESS_LOCK:
                    DOWNLOAD_PROGRESS[download_id] = {
                        "download_id": download_id,
                        "state": "running",
                        "obsid": obsid,
                        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "downloaded_bytes": 0,
                        "total_bytes": total_bytes,
                        "current_file": "",
                        "file_index": 0,
                        "file_count": len(chosen),
                    }
                files = []
                downloaded_total = 0
                for i, p in enumerate(chosen, start=1):
                    filename = str(p.get("productFilename") or "")
                    expected = int(p.get("size") or 0)
                    with DOWNLOAD_PROGRESS_LOCK:
                        state = DOWNLOAD_PROGRESS.get(download_id, {})
                        state.update(
                            {
                                "state": "running",
                                "current_file": filename,
                                "file_index": i,
                                "file_count": len(chosen),
                                "downloaded_bytes": downloaded_total,
                                "total_bytes": total_bytes or state.get("total_bytes", 0),
                            }
                        )
                        DOWNLOAD_PROGRESS[download_id] = state

                    def on_progress(done: int, total: int, base: int = downloaded_total, guess: int = expected) -> None:
                        est_total = total if total > 0 else guess
                        overall = base + max(0, min(done, est_total if est_total > 0 else done))
                        with DOWNLOAD_PROGRESS_LOCK:
                            state = DOWNLOAD_PROGRESS.get(download_id, {})
                            state.update(
                                {
                                    "state": "running",
                                    "current_file": filename,
                                    "file_index": i,
                                    "file_count": len(chosen),
                                    "downloaded_bytes": overall,
                                    "total_bytes": total_bytes or state.get("total_bytes", 0),
                                }
                            )
                            DOWNLOAD_PROGRESS[download_id] = state

                    out = download_product(p, out_dir, dry_run=dry_run, progress_cb=on_progress)
                    actual_size = out.stat().st_size if out.exists() else expected
                    downloaded_total += expected or actual_size
                    files.append(
                        {
                            "productFilename": p.get("productFilename"),
                            "productSubGroupDescription": p.get("productSubGroupDescription"),
                            "size_mb": round(int(p.get("size") or 0) / (1024 * 1024), 2),
                            "output_path": str(out),
                            "dry_run": dry_run,
                        }
                    )
                with DOWNLOAD_PROGRESS_LOCK:
                    state = DOWNLOAD_PROGRESS.get(download_id, {})
                    state.update(
                        {
                            "state": "done",
                            "current_file": "",
                            "file_index": len(chosen),
                            "file_count": len(chosen),
                            "downloaded_bytes": total_bytes or downloaded_total,
                            "total_bytes": total_bytes or downloaded_total,
                            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        }
                    )
                    DOWNLOAD_PROGRESS[download_id] = state
                self._send_json({"obsid": obsid, "download_id": download_id, "downloaded": files})
            except Exception as e:
                try:
                    if 'download_id' in locals() and download_id:
                        with DOWNLOAD_PROGRESS_LOCK:
                            state = DOWNLOAD_PROGRESS.get(download_id, {"download_id": download_id})
                            state.update(
                                {
                                    "state": "error",
                                    "error": str(e),
                                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                }
                            )
                            DOWNLOAD_PROGRESS[download_id] = state
                except Exception:
                    pass
                self._send_json({"error": str(e)}, status=500)

    host = getattr(args, "host", "127.0.0.1")
    requested_port = int(getattr(args, "port", 8765))
    no_browser = bool(getattr(args, "no_browser", False))
    try:
        server = http.server.ThreadingHTTPServer((host, requested_port), Handler)
    except OSError:
        # Port in use. Fall back to an ephemeral local port instead of dying.
        server = http.server.ThreadingHTTPServer((host, 0), Handler)
        print(f"Port {requested_port} unavailable; using {server.server_address[1]} instead.")
    port = int(server.server_address[1])
    url = f"http://{host}:{port}/"
    print(f"Freshest Photons GUI running at {url}")
    print("Ctrl+C to stop.")
    if not no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\\nStopping GUI.")
    finally:
        server.server_close()
    return 0


def main() -> int:
    args = parse_args()
    cmd = args.cmd or ("gui" if len(sys.argv) == 1 else "latest")
    if cmd == "latest":
        return list_cmd(args)
    if cmd == "download":
        return download_cmd(args)
    if cmd == "gui":
        return gui_cmd(args)
    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
