#!/usr/bin/env python3
"""Recording reverse proxy: client -> :LISTEN -> TARGET. Saves raw request and
raw response bytes (streamed through unchanged) for every exchange."""
import os, sys, threading, http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
LISTEN = int(os.environ.get("LISTEN", "11435"))
THOST, TPORT = os.environ.get("TARGET", "127.0.0.1:11434").split(":")
CAPDIR = os.environ.get("CAPDIR", "captures"); os.makedirs(CAPDIR, exist_ok=True)
lock = threading.Lock(); counter = [0]
class P(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _go(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        with lock:
            counter[0] += 1; n = counter[0]
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "accept-encoding")}; hdrs["Host"] = THOST
        with open(f"{CAPDIR}/exchange-{n:03d}-request.http", "wb") as f:
            f.write(f"{self.command} {self.path} HTTP/1.1\r\n".encode())
            for k, v in self.headers.items():
                f.write(f"{k}: {'<redacted>' if k.lower() in ('authorization','x-api-key') else v}\r\n".encode())
            f.write(b"\r\n" + body)
        conn = (http.client.HTTPSConnection(THOST, int(TPORT), timeout=600) if os.environ.get("TARGET_TLS") else http.client.HTTPConnection(THOST, int(TPORT), timeout=600))
        conn.request(self.command, self.path, body=body, headers=hdrs)
        r = conn.getresponse()
        out = open(f"{CAPDIR}/exchange-{n:03d}-response.http", "wb")
        out.write(f"HTTP/1.1 {r.status} {r.reason}\r\n".encode())
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() in ("content-length", "transfer-encoding", "connection"): continue
            self.send_header(k, v); out.write(f"{k}: {v}\r\n".encode())
        self.send_header("Transfer-Encoding", "chunked"); self.end_headers(); out.write(b"\r\n")
        while True:
            data = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
            if not data: break
            out.write(data); out.flush()
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n"); self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n"); self.wfile.flush(); out.close(); conn.close()
    do_POST = _go; do_GET = _go
ThreadingHTTPServer(("127.0.0.1", LISTEN), P).serve_forever()
