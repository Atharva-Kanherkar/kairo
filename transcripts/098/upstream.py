#!/usr/bin/env python3
"""Deterministic OpenAI-compatible upstream for the 098 image-route rig.

Every POST is appended to a JSONL ledger before the response is sent, so the
caller can prove which deployment the proxy actually reached. Image routes get
an OpenAI Images response whose ``revised_prompt`` names the upstream; chat
routes get a chat completion. No network access and no provider credentials.
"""

import base64
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    label = "UNSET"
    ledger = None

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        entry = {
            "method": "POST",
            "path": self.path,
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body_b64": base64.b64encode(raw).decode("ascii"),
        }
        with open(self.ledger, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

        if "images" in self.path:
            response = {
                "created": 1700000000,
                "data": [
                    {
                        "url": "https://example.invalid/%s.png" % self.label,
                        "revised_prompt": "SERVED_BY_%s_IMAGE" % self.label,
                    }
                ],
            }
        else:
            try:
                request = json.loads(raw)
            except Exception:
                request = {}
            response = {
                "id": "chatcmpl-%s" % self.label,
                "object": "chat.completion",
                "created": 1700000000,
                "model": request.get("model", "fake"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "SERVED_BY_%s" % self.label,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    port = int(sys.argv[1])
    Handler.label = sys.argv[2]
    Handler.ledger = sys.argv[3]
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
