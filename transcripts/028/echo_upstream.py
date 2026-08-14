#!/usr/bin/env python3
"""Capture mock: echo inbound query `key` into Location / x-goog-upload-url / JSON.

Used to see whether a proxy copies Google-style credential URLs back to the caller.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9996
OUT = sys.argv[2] if len(sys.argv) > 2 else "transcripts/028/cap-echo.jsonl"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _handle(self):
        n = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(n) if n else b""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        key = (qs.get("key") or [""])[0]
        rec = {
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": raw.decode("utf-8", "replace")[:2000],
            "query_key_present": bool(key),
        }
        with open(OUT, "a") as f:
            f.write(json.dumps(rec) + "\n")

        loc = f"http://127.0.0.1:{PORT}/redirected?key={key}&from=location"
        upload = f"http://127.0.0.1:{PORT}/upload?upload_id=canary&key={key}"
        body = json.dumps(
            {
                "ok": True,
                "echo_path": parsed.path,
                "echo_key_in_body": key,
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("Location", loc)
        self.send_header("x-goog-upload-url", upload)
        self.send_header("x-goog-upload-status", "active")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def do_DELETE(self):
        self._handle()


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
