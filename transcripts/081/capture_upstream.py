"""Small OpenAI-compatible capture server used by the OGX 1.4.0 repro.

It writes only parsed request bodies, never request headers or credentials.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


PORT = int(sys.argv[1])
OUT = sys.argv[2]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, body, content_type="application/json"):
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") != "/v1/models":
            self.send_error(404)
            return
        self._send(json.dumps({
            "object": "list",
            "data": [{"id": "mock-gpt", "object": "model", "owned_by": "kairo"}],
        }).encode())

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400)
            return
        with open(OUT, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"ts": time.time(), "path": self.path, "body": body}) + "\n")
        response = {
            "id": "chatcmpl-kairo-081",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "mock-gpt"),
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": "Synthetic control response."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        self._send(json.dumps(response).encode())


if __name__ == "__main__":
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
