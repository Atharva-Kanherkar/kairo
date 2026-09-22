"""Small OpenAI-compatible capture server used by the OGX 1.4.0 repro.

It writes parsed request bodies plus the exact request and response bodies.
Headers and credentials are never captured.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


PORT = int(sys.argv[1])
OUT = sys.argv[2]
RAW_DIR = sys.argv[3] if len(sys.argv) > 3 else None
POST_COUNT = 0


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
        global POST_COUNT
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
            stream.write(json.dumps({"path": self.path, "body": body}) + "\n")
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
        response_raw = json.dumps(response).encode()
        POST_COUNT += 1
        if RAW_DIR:
            raw_dir = os.path.abspath(RAW_DIR)
            os.makedirs(raw_dir, exist_ok=True)
            with open(os.path.join(raw_dir, f"forwarded-{POST_COUNT:03d}-request.json"), "wb") as stream:
                stream.write(raw)
            with open(os.path.join(raw_dir, f"upstream-{POST_COUNT:03d}-response.json"), "wb") as stream:
                stream.write(response_raw)
        self._send(response_raw)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
