# Capture server: records method, path, headers, and body. Replies canned JSON.
# Used to see what a gateway actually forwards upstream, including headers.
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9996
OUT = sys.argv[2] if len(sys.argv) > 2 else "capture-headers.jsonl"
CANNED_PATH = sys.argv[3] if len(sys.argv) > 3 else None

CANNED = {
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


class H(BaseHTTPRequestHandler):
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
        with open(OUT, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _reply(self):
        if CANNED_PATH:
            resp = open(CANNED_PATH, "rb").read()
        else:
            resp = json.dumps(CANNED).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def do_POST(self):
        self._record()
        self._reply()

    def do_GET(self):
        self._record()
        self._reply()

    def do_PUT(self):
        self._record()
        self._reply()


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
