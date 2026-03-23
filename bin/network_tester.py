#!/usr/bin/env python3

import argparse
import json
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from typing import Any


DEFAULT_PING_HOST = "1.1.1.1"
DEFAULT_DNS_HOST = "google.com"
DEFAULT_DOWNLOAD_URL = "https://speed.cloudflare.com/__down?bytes=25000000"


def run_command(command: list[str], timeout: float) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def parse_ping_output(output: str) -> dict[str, Any]:
    latency_values: list[float] = []
    packet_loss: float | None = None
    transmitted: int | None = None
    received: int | None = None
    minimum: float | None = None
    average: float | None = None
    maximum: float | None = None
    jitter: float | None = None

    for line in output.splitlines():
        line = line.strip()
        if "time=" in line:
            for piece in line.split():
                if piece.startswith("time="):
                    latency_values.append(float(piece.split("=", 1)[1]))
                    break
        if "packets transmitted" in line and "packet loss" in line:
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 3:
                transmitted = int(parts[0].split()[0])
                received = int(parts[1].split()[0])
                packet_loss = float(parts[2].split("%", 1)[0])
        if "min/avg/max" in line or "round-trip min/avg/max" in line:
            stats_part = line.split("=", 1)[1].strip().split()[0]
            values = [float(value) for value in stats_part.split("/")]
            if len(values) >= 4:
                minimum, average, maximum, jitter = values[:4]

    result: dict[str, Any] = {
        "samples_ms": latency_values,
        "sent": transmitted,
        "received": received,
        "packet_loss_percent": packet_loss,
        "min_ms": minimum,
        "avg_ms": average,
        "max_ms": maximum,
        "jitter_ms": jitter,
    }
    if latency_values:
        result["median_ms"] = statistics.median(latency_values)
        result["stdev_ms"] = statistics.pstdev(latency_values)
    return result


def ping_host(host: str, count: int, timeout: float) -> dict[str, Any]:
    if not shutil.which("ping"):
        return {"host": host, "ok": False, "error": "ping command not found"}

    command = ["ping", "-n", "-c", str(count), "-W", str(max(1, int(timeout))), host]
    started = time.perf_counter()
    try:
        returncode, stdout, stderr = run_command(command, timeout=(count * timeout) + 5)
    except subprocess.TimeoutExpired:
        return {"host": host, "ok": False, "error": "ping timed out"}
    elapsed_ms = (time.perf_counter() - started) * 1000

    parsed = parse_ping_output(stdout)
    return {
        "host": host,
        "ok": returncode == 0,
        "elapsed_ms": round(elapsed_ms, 1),
        "command": command,
        "stderr": stderr.strip() or None,
        **parsed,
    }


def dns_lookup(host: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        addresses = socket.getaddrinfo(host, None)
    except socket.gaierror as error:
        return {"host": host, "ok": False, "error": str(error)}
    elapsed_ms = (time.perf_counter() - started) * 1000
    unique_ips = sorted({entry[4][0] for entry in addresses})
    return {
        "host": host,
        "ok": True,
        "lookup_ms": round(elapsed_ms, 1),
        "addresses": unique_ips,
    }


def http_download(url: str, bytes_to_read: int, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "network_tester/1.0"},
    )
    started = time.perf_counter()
    first_byte_ms: float | None = None
    bytes_read = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            while bytes_read < bytes_to_read:
                chunk = response.read(min(1024 * 1024, bytes_to_read - bytes_read))
                if not chunk:
                    break
                if first_byte_ms is None:
                    first_byte_ms = (time.perf_counter() - started) * 1000
                bytes_read += len(chunk)
    except Exception as error:
        return {"url": url, "ok": False, "error": str(error)}

    elapsed_s = time.perf_counter() - started
    bits_per_second = (bytes_read * 8) / elapsed_s if elapsed_s > 0 else 0
    return {
        "url": url,
        "ok": True,
        "bytes_read": bytes_read,
        "time_to_first_byte_ms": round(first_byte_ms or 0, 1),
        "elapsed_s": round(elapsed_s, 3),
        "download_mbps": round(bits_per_second / 1_000_000, 2),
    }


def summarize(results: dict[str, Any]) -> str:
    lines = []
    internet_ping = results["internet_ping"]
    dns_ping = results["dns_ping"]
    dns_lookup_result = results["dns_lookup"]
    download = results["download"]

    if internet_ping["ok"]:
        lines.append(
            f"Internet ping {internet_ping['host']}: avg {internet_ping.get('avg_ms')} ms, "
            f"loss {internet_ping.get('packet_loss_percent')}%, jitter {internet_ping.get('jitter_ms')} ms"
        )
    else:
        lines.append(f"Internet ping {internet_ping['host']}: failed ({internet_ping.get('error') or internet_ping.get('stderr')})")

    if dns_lookup_result["ok"]:
        lines.append(
            f"DNS lookup {dns_lookup_result['host']}: {dns_lookup_result['lookup_ms']} ms, "
            f"{len(dns_lookup_result['addresses'])} address(es)"
        )
    else:
        lines.append(f"DNS lookup {dns_lookup_result['host']}: failed ({dns_lookup_result.get('error')})")

    if dns_ping["ok"]:
        lines.append(
            f"Named host ping {dns_ping['host']}: avg {dns_ping.get('avg_ms')} ms, "
            f"loss {dns_ping.get('packet_loss_percent')}%"
        )
    else:
        lines.append(f"Named host ping {dns_ping['host']}: failed ({dns_ping.get('error') or dns_ping.get('stderr')})")

    if download["ok"]:
        lines.append(
            f"HTTP download: {download['download_mbps']} Mbps, "
            f"TTFB {download['time_to_first_byte_ms']} ms, {download['bytes_read']} bytes"
        )
    else:
        lines.append(f"HTTP download: failed ({download.get('error')})")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quick network tester for latency, loss, DNS, and download speed.")
    parser.add_argument("--ping-host", default=DEFAULT_PING_HOST, help="Host/IP for raw internet ping test")
    parser.add_argument("--dns-host", default=DEFAULT_DNS_HOST, help="Host for DNS lookup and named-host ping")
    parser.add_argument("--ping-count", type=int, default=12, help="Ping sample count")
    parser.add_argument("--timeout", type=float, default=2.0, help="Per-request timeout in seconds")
    parser.add_argument(
        "--download-url",
        default=DEFAULT_DOWNLOAD_URL,
        help="URL used for download speed sampling",
    )
    parser.add_argument(
        "--download-bytes",
        type=int,
        default=8_000_000,
        help="How many bytes to read for download speed estimation",
    )
    parser.add_argument("--json", action="store_true", help="Output full JSON instead of summary text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results = {
        "timestamp": int(time.time()),
        "internet_ping": ping_host(args.ping_host, args.ping_count, args.timeout),
        "dns_lookup": dns_lookup(args.dns_host),
        "dns_ping": ping_host(args.dns_host, args.ping_count, args.timeout),
        "download": http_download(args.download_url, args.download_bytes, args.timeout + 20),
    }

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(summarize(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
