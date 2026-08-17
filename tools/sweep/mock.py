# Sweep capture mock: records method, path, headers, and body for every
# upstream request, and replies with whatever canned response the runner has
# staged. Deliberately separate from tools/capture_headers.py: that file is
# frozen evidence-producing tooling cited by existing issue writeups, and
# changing it would invalidate their repro blocks.
#
# Two properties the sweep needs that capture_headers.py does not have:
#   1. The canned reply is re-read from disk on every request, so the runner
#      can stage a different upstream response per probe without a restart.
#   2. SSE replies, for the streaming probes.
#
# Usage: python3 -m tools.sweep.mock PORT OUTFILE CANNED_PATH
#   CANNED_PATH ending in .sse is streamed as text/event-stream.
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_JSON = {
    "id": "chatcmpl-sweep",
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


def make_handler(outfile, canned_path):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _record(self):
            n = int(self.headers.get("content-length", 0))
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
            with open(outfile, "a") as f:
                f.write(json.dumps(rec) + "\n")

        def _reply(self):
            try:
                data = open(canned_path, "rb").read()
            except OSError:
                data = json.dumps(DEFAULT_JSON).encode()
            if canned_path.endswith(".sse"):
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

        def do_PUT(self):
            self._record()
            self._reply()

    return H


def serve(port, outfile, canned_path):
    HTTPServer(("127.0.0.1", port), make_handler(outfile, canned_path)).serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]), sys.argv[2], sys.argv[3])
