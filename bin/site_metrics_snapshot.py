#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

LOG_PATH = Path("/tmp/pkd_caddy_access.log")
OUTPUT_PATH = Path("/home/david/random/www/site-metrics.json")
TARGET_HOST = "sol.system42.one"
EXCLUDED_URIS = {
    "/site-metrics.json",
    "/site-metrics.html",
    "/api/logbook/messages",
    "/posts/index.json",
}
SUPPRESSED_PREFIXES = tuple(sorted(EXCLUDED_URIS))
SELF_IPS = {
    "10.0.1.89",
    "127.0.0.1",
    "::1",
    "2600:100c:a214:f565:2164:c32b:3d59:e921",
}


def classify_source(client_ip: str, user_agent: str) -> str:
    ua = (user_agent or "").lower()
    ip = (client_ip or "").strip().lower()
    if ip in {value.lower() for value in SELF_IPS}:
        return "self"
    if ip.startswith("10.") or ip.startswith("192.168.") or ip.startswith("172.16.") or ip.startswith("172.17.") or ip.startswith("172.18.") or ip.startswith("172.19.") or ip.startswith("172.2") or ip.startswith("fd") or ip.startswith("fe80:"):
        return "internal lan"
    if "facebookexternalhit" in ua:
        return "facebook crawler"
    if "meta-externalagent" in ua:
        return "meta crawler"
    if "twitterbot" in ua or "x-twitterbot" in ua:
        return "twitter crawler"
    if "slackbot" in ua:
        return "slack crawler"
    if "bot" in ua or "crawler" in ua or "spider" in ua or "python-requests" in ua or "curl/" in ua or "wget/" in ua:
        return "other bot"
    if "chrome/" in ua or "safari/" in ua or "firefox/" in ua or "edg/" in ua:
        return "external browser"
    return "unknown"


def has_query_tag(uri: str, key: str) -> bool:
    if not uri:
        return False
    return f"{key}=" in uri.lower()


def parse_log_line(line: str) -> dict | None:
    start = line.find('{"request":')
    if start == -1:
        return None
    try:
        return json.loads(line[start:])
    except json.JSONDecodeError:
        return None


def extract_client_ip(headers: dict[str, list[str]]) -> str:
    for key in ("Cf-Connecting-Ip", "cf-connecting-ip", "X-Forwarded-For", "x-forwarded-for"):
        values = headers.get(key)
        if values:
            return values[0].split(",")[0].strip()
    return ""


def normalize_path(uri: str) -> str:
    if not uri:
        return ""
    return urlsplit(uri).path or uri.split("?", 1)[0]


def main() -> int:
    requests: list[dict] = []
    if LOG_PATH.exists():
        for raw_line in LOG_PATH.read_text(errors="ignore").splitlines():
            payload = parse_log_line(raw_line)
            if not payload:
                continue
            request = payload.get("request", {})
            host = request.get("host", "")
            uri = request.get("uri", "")
            path = normalize_path(uri)
            if host != TARGET_HOST or path in EXCLUDED_URIS:
                continue
            headers = request.get("headers", {}) or {}
            requests.append(
                {
                    "uri": uri,
                    "path": path,
                    "method": request.get("method", ""),
                    "status": payload.get("status"),
                    "client_ip": extract_client_ip(headers),
                    "user_agent": (headers.get("User-Agent") or headers.get("user-agent") or [""])[0],
                }
            )

    visible_requests = [
        item for item in requests
        if not any(item["path"].startswith(prefix) for prefix in SUPPRESSED_PREFIXES)
    ]

    path_counts = Counter(item["path"] for item in visible_requests)
    ip_counts = Counter(item["client_ip"] for item in visible_requests if item["client_ip"])
    status_counts = Counter(str(item["status"]) for item in visible_requests if item["status"] is not None)

    for item in visible_requests:
        item["source_type"] = classify_source(item["client_ip"], item["user_agent"])

    source_counts = Counter(item["source_type"] for item in visible_requests)
    external_requests = [item for item in visible_requests if item["source_type"] not in {"self", "internal lan"}]
    external_path_counts = Counter(item["path"] for item in external_requests)
    external_ip_counts = Counter(item["client_ip"] for item in external_requests if item["client_ip"])
    tagged_counts = Counter()
    for item in external_requests:
        if has_query_tag(item["uri"], "fbclid"):
            tagged_counts["fbclid"] += 1
        if has_query_tag(item["uri"], "utm_"):
            tagged_counts["utm"] += 1

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": TARGET_HOST,
        "total_hits": len(visible_requests),
        "unique_ips": len(ip_counts),
        "external_hits": len(external_requests),
        "external_unique_ips": len(external_ip_counts),
        "top_paths": [
            {"path": path, "hits": hits}
            for path, hits in path_counts.most_common(12)
        ],
        "top_external_paths": [
            {"path": path, "hits": hits}
            for path, hits in external_path_counts.most_common(8)
        ],
        "top_ips": [
            {"ip": ip, "hits": hits}
            for ip, hits in ip_counts.most_common(8)
        ],
        "source_types": [
            {"label": label, "hits": hits}
            for label, hits in source_counts.most_common(8)
        ],
        "status_codes": [
            {"code": code, "hits": hits}
            for code, hits in status_counts.most_common(8)
        ],
        "query_tags": [
            {"label": label, "hits": hits}
            for label, hits in tagged_counts.most_common(6)
        ],
        "recent_requests": visible_requests[-20:],
        "suppressed_paths": sorted(EXCLUDED_URIS),
    }

    OUTPUT_PATH.write_text(json.dumps(snapshot, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
