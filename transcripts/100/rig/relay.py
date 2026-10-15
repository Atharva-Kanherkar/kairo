"""Logging HTTP relay for the 100 rig. Records each exchange, then relays it.

Usage: relay.py LISTEN_PORT UPSTREAM_BASE OUT_DIR

Every request and response is written as NNN-request.http / NNN-response.http
before the response is returned. Redaction applies to the written copy only;
the relayed bytes are unchanged. Redactions are marked inline:

- credential headers (x-api-key, authorization, cookie, api-key, x-goog-api-key)
- tenant headers returned by Anthropic (anthropic-organization-id,
  anthropic-workspace-id)
- the Claude Code device id inside metadata.user_id
"""
import http.client
import itertools
import os
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
UPSTREAM = urllib.parse.urlsplit(sys.argv[2])
OUT = sys.argv[3]
os.makedirs(OUT, exist_ok=True)
CREDENTIAL_HEADERS = {"x-api-key", "authorization", "cookie", "x-goog-api-key", "api-key"}
TENANT_HEADERS = {"anthropic-organization-id", "anthropic-workspace-id"}
HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding", "keep-alive"}
DEVICE_ID = re.compile(rb'(device_id[\\"\s:]+)[0-9a-f]{32,}')
counter = itertools.count(1)
lock = threading.Lock()


def render_headers(items):
    lines = []
    for name, value in items:
        if name.lower() in CREDENTIAL_HEADERS:
            value = "[REDACTED: credential]"
        elif name.lower() in TENANT_HEADERS:
            value = "[REDACTED: tenant id]"
        lines.append(f"{name}: {value}")
    return "\r\n".join(lines).encode()


def redact_body(body):
    return DEVICE_ID.sub(rb"\1[REDACTED: device id]", body)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.relay()

    def do_POST(self):
        self.relay()

    def relay(self):
        with lock:
            n = next(counter)
        length = int(self.headers.get("content-length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        prefix = os.path.join(OUT, f"{n:03d}")
        with open(prefix + "-request.http", "wb") as f:
            f.write(f"{self.command} {self.path} HTTP/1.1\r\n".encode())
            f.write(render_headers(self.headers.items()) + b"\r\n\r\n")
            f.write(redact_body(body))
        conn_cls = (
            http.client.HTTPSConnection if UPSTREAM.scheme == "https" else http.client.HTTPConnection
        )
        conn = conn_cls(UPSTREAM.hostname, UPSTREAM.port, timeout=600)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["host"] = UPSTREAM.netloc
        conn.request(self.command, UPSTREAM.path.rstrip("/") + self.path, body=body or None, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        with open(prefix + "-response.http", "wb") as f:
            f.write(f"HTTP/1.1 {resp.status} {resp.reason}\r\n".encode())
            f.write(render_headers(resp.getheaders()) + b"\r\n\r\n")
            f.write(redact_body(data))
        self.send_response(resp.status)
        for name, value in resp.getheaders():
            if name.lower() not in HOP and name.lower() != "content-encoding":
                self.send_header(name, value)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
