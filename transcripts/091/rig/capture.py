#!/usr/bin/env python3
"""Deterministic capture upstream.

Records the exact bytes of every forwarded request to a JSONL file, and replies
with a canned body re-read from disk on each request so a runner can change the
reply without a restart.

Usage: capture.py PORT OUTFILE [CANNED]

Env:
  CANNED_POINTER  if set, the file it names is preferred over CANNED. Lets a
                  runner stage a different reply per case with no restart.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The pointer is an absolute path when CANNED_POINTER is set, because the
# pointer file is written by the runner and read by this server, and the two do
# not share a working directory. A bare relative name resolved against this
# process's cwd silently pointed at a stale or missing file.
POINTER = os.environ.get("CANNED_POINTER") or "canned.pointer"

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

DEFAULT_RESPONSES = {
    "id": "resp_capture",
    "object": "response",
    "created_at": 0,
    "model": "captured",
    "status": "completed",
    "output": [{
        "type": "message",
        "id": "msg_0",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "ok", "annotations": []}],
    }],
    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
}

DEFAULT_ANTHROPIC = {
    "id": "msg_capture",
    "type": "message",
    "role": "assistant",
    "model": "captured",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


def resolve_canned(default_path):
    try:
        with open(POINTER) as f:
            named = f.read().strip()
        if named and os.path.exists(named):
            return named
    except OSError:
        pass
    return default_path


def make_handler(outfile, canned_path):
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _record(self, body):
            entry = {
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body.decode("utf-8", "replace"),
            }
            with lock:
                with open(outfile, "a") as f:
                    f.write(json.dumps(entry) + "\n")

        def do_GET(self):
            self._record(b"")
            payload = json.dumps(DEFAULT_JSON).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n) if n else b""
            self._record(body)

            path = self.path
            canned = resolve_canned(canned_path)
            payload = b""
            ctype = "application/json"
            try:
                with open(canned, "rb") as f:
                    payload = f.read()
            except OSError:
                if "/responses" in path:
                    payload = json.dumps(DEFAULT_RESPONSES).encode()
                elif "/messages" in path:
                    payload = json.dumps(DEFAULT_ANTHROPIC).encode()
                else:
                    payload = json.dumps(DEFAULT_JSON).encode()

            if canned.endswith(".sse"):
                ctype = "text/event-stream"
                self.send_response(200)
                self.send_header("content-type", ctype)
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                for line in payload.split(b"\n"):
                    chunk = line + b"\n"
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.write(b"0\r\n\r\n")
                return

            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return H


def main():
    port = int(sys.argv[1])
    outfile = sys.argv[2]
    canned = sys.argv[3] if len(sys.argv) > 3 else ""
    os.makedirs(os.path.dirname(os.path.abspath(outfile)) or ".", exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(outfile, canned))
    srv.serve_forever()


if __name__ == "__main__":
    main()