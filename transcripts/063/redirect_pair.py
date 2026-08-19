#!/usr/bin/env python3
"""307/302 pair: origin redirects, sink records headers. No real keys."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

mode = sys.argv[1]  # origin | sink
port = int(sys.argv[2])
out = sys.argv[3]
dest = sys.argv[4] if len(sys.argv) > 4 else ""
status = int(sys.argv[5]) if len(sys.argv) > 5 else 307

CANNED_OA = {
    "id": "chatcmpl-cap",
    "object": "chat.completion",
    "created": 0,
    "model": "captured",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "ok"},
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
CANNED_ANTH = {
    "id": "msg_cap",
    "type": "message",
    "role": "assistant",
    "model": "cap",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _record(self, kind):
        n = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        rec = {
            "kind": kind,
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body_len": len(raw),
        }
        with open(out, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def do_POST(self):
        if mode == "origin":
            self._record("origin")
            self.send_response(status)
            self.send_header("Location", dest)
            self.end_headers()
            return
        self._record("sink")
        canned = CANNED_ANTH if "messages" in self.path else CANNED_OA
        resp = json.dumps(canned).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def do_GET(self):
        self.do_POST()


HTTPServer(("127.0.0.1", port), H).serve_forever()
