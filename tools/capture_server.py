# Minimal capture server: logs the exact request body Switchyard sends upstream,
# returns a canned OpenAI chat completion so the translation round-trips.
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

OUT = sys.argv[1] if len(sys.argv) > 1 else "capture.jsonl"
CANNED = {
    "id": "chatcmpl-cap", "object": "chat.completion", "created": 0,
    "model": "captured", "choices": [{"index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = self.rfile.read(n)
        with open(OUT, "a") as f:
            f.write(json.dumps({"path": self.path,
                "body": json.loads(body) if body else None}) + "\n")
        resp = json.dumps(CANNED).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

HTTPServer(("127.0.0.1", 9999), H).serve_forever()
