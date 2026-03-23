#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from logbook_irc_common import (
    IRCError,
    clean_channel,
    clean_message,
    clean_name,
    ensure_store,
    recent_messages,
    send_irc_message,
)

HOST = "127.0.0.1"
PORT = 8890
MAX_MESSAGES = 250
RATE_LIMIT_WINDOW = 300
RATE_LIMIT_COUNT = 4

rate_limit: dict[str, deque[float]] = defaultdict(deque)


def allowed(ip: str) -> bool:
    bucket = rate_limit[ip]
    cutoff = time.time() - RATE_LIMIT_WINDOW
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_COUNT:
        return False
    bucket.append(time.time())
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "SolLogbook/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            return forwarded
        return self.client_address[0]

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/messages":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        params = parse_qs(parsed.query)
        try:
            limit = max(1, min(int(params.get("limit", ["80"])[0]), MAX_MESSAGES))
        except ValueError:
            limit = 80
        channel = clean_channel(params.get("channel", ["public-logbook"])[0])
        self._json(
            HTTPStatus.OK,
            {
                "messages": recent_messages(limit, channel),
                "limit": limit,
                "channel": channel,
            },
        )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/messages":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        ip = self._client_ip()
        if not allowed(ip):
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate_limited"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 8192:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_length"})
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        name = clean_name(payload.get("name"))
        message = clean_message(payload.get("message"))
        channel = clean_channel(payload.get("channel"))

        if not name:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "name_required"})
            return
        if not message:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "message_required"})
            return

        try:
            send_irc_message(name, channel, message)
        except IRCError as exc:
            error = str(exc)
            status = HTTPStatus.CONFLICT if error == "name_in_use" else HTTPStatus.BAD_GATEWAY
            self._json(status, {"error": error})
            return

        self._json(
            HTTPStatus.CREATED,
            {
                "ok": True,
                "message": {
                    "name": name,
                    "message": message,
                    "channel": channel,
                },
            },
        )


def main() -> None:
    ensure_store()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"logbook api listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
