#!/usr/bin/env python3
"""Deterministic fault-injecting OpenAI-compatible upstream for kairo 087.

One chat-completions endpoint. The first call of a turn (no tool result in the
request) returns a single tool call to the MCP tool. The follow-up call (the
request carries a tool result) returns a retryable HTTP 500 when FAULT=on, or a
plain text answer when FAULT=off. Every request and response is frozen under
CAPTURE_DIR with the provider credential replaced by an inline marker.

The tool name matches what LiteLLM injects for an MCP server named
`kairoledger`, namely `kairoledger-commit_write`.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ["UPSTREAM_PORT"])
CAPTURE = Path(os.environ["CAPTURE_DIR"])
FAULT = os.environ.get("FAULT", "on")
MODEL = "kairo-087-model"
TOOL_NAME = "kairoledger-commit_write"
SYNTHETIC_UPSTREAM_KEY = "sk-kairo-" + "087-upstream-only"
LOCK = threading.Lock()
COUNTER = 0


def compact(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def is_followup(body):
    try:
        data = json.loads(body)
    except ValueError:
        return False
    for message in data.get("messages", []) or []:
        if isinstance(message, dict) and message.get("role") == "tool":
            return True
    return False


def tool_call_response():
    return {
        "id": "chatcmpl-087-initial",
        "object": "chat.completion",
        "created": 1790000000,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_087_a",
                            "type": "function",
                            "function": {
                                "name": TOOL_NAME,
                                "arguments": compact({"entry": "alpha"}),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def text_response():
    return {
        "id": "chatcmpl-087-final",
        "object": "chat.completion",
        "created": 1790000000,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "KAIRO087_FINAL done"},
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }


def sanitize(text):
    return text.replace(SYNTHETIC_UPSTREAM_KEY, "<SYNTHETIC_UPSTREAM_KEY>")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _freeze(self, kind, request_body, status, response_body):
        global COUNTER
        with LOCK:
            COUNTER += 1
            sequence = COUNTER
        stem = CAPTURE / ("upstream-%03d" % sequence)
        stem.with_name(stem.name + ".json").write_text(
            compact(
                {
                    "sequence": sequence,
                    "kind": kind,
                    "status": status,
                    "followup": is_followup(request_body),
                    "path": self.path,
                }
            )
        )
        header_lines = []
        for name, value in self.headers.items():
            if name.lower() == "authorization":
                value = "Bearer <SYNTHETIC_UPSTREAM_KEY>"
            else:
                value = sanitize(value)
            header_lines.append("%s: %s" % (name, value))
        stem.with_name(stem.name + "-request.http").write_text(
            "POST %s HTTP/1.1\r\n" % self.path
            + "\r\n".join(header_lines)
            + "\r\n\r\n"
            + sanitize(request_body)
        )
        stem.with_name(stem.name + "-response.http").write_text(
            "HTTP/1.1 %d\r\ncontent-type: application/json\r\n\r\n" % status
            + sanitize(response_body)
        )

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        request_body = self.rfile.read(length).decode("utf-8", "replace")
        followup = is_followup(request_body)
        if followup and FAULT == "on":
            payload = compact(
                {
                    "error": {
                        "message": "kairo087 injected upstream error",
                        "type": "server_error",
                        "code": "500",
                    }
                }
            )
            self._freeze("followup-fault", request_body, 500, payload)
            self._send(500, payload)
            return
        payload = compact(text_response() if followup else tool_call_response())
        self._freeze("followup-ok" if followup else "initial", request_body, 200, payload)
        self._send(200, payload)

    def _send(self, status, payload):
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload.encode())))
        self.end_headers()
        self.wfile.write(payload.encode())


if __name__ == "__main__":
    CAPTURE.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
