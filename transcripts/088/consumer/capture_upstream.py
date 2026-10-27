#!/usr/bin/env python3
"""Deterministic capture upstream for the agentgateway rig.

Records the exact bytes of every upstream request to a JSONL file, and replies
with a canned response re-read from disk on each request so the runner can
change the reply without a restart.

Usage: capture.py PORT OUTFILE CANNED   (see canned.pointer)
  CANNED ending in .sse is sent as text/event-stream.
Env:
  CANNED_DIR  if set, a file named <CANNED_DIR>/<basename CANNED> is preferred,
              letting a runner stage a different reply per case.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_JSON = {
    "id": "chatcmpl-capture",
    "object": "chat.completion",
    "created": 0,
    "model": "captured",
    "choices": [{
        "index": 0,
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": "ok"},
    }],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


POINTER = "canned.pointer"


def resolve_canned(canned_path):
    """A `canned.pointer` file names the reply to send. Re-read per request so a
    runner can restage without a restart, and so the pointer can switch between
    a .json and a .sse reply (which a fixed directory lookup cannot do)."""
    try:
        with open(POINTER) as f:
            named = f.read().strip()
        if named and os.path.exists(named):
            return named
    except OSError:
        pass
    return canned_path


def make_handler(outfile, canned_path):
    write_lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _record(self):
            n = int(self.headers.get("content-length", 0) or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = {"_raw": raw.decode("utf-8", "replace")}
            rec = {
                "method": self.command,
                "path": self.path,
                "headers": {k: v for k, v in self.headers.items()},
                "body": body,
            }
            with write_lock, open(outfile, "a") as f:
                f.write(json.dumps(rec) + "\n")

        def _reply(self):
            path = resolve_canned(canned_path)
            try:
                data = open(path, "rb").read()
            except OSError:
                data = json.dumps(DEFAULT_JSON).encode()
            try:
                self._write(data, path)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _write(self, data, path):
            if path.endswith(".sse"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self._record()
            self._reply()

        def do_GET(self):
            self._record()
            self._reply()

    return H


if __name__ == "__main__":
    port, outfile, canned = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(outfile, canned))
    srv.daemon_threads = True
    print(f"capture mock on 127.0.0.1:{port} -> {outfile}", flush=True)
    srv.serve_forever()
