#!/usr/bin/env python3
"""Deterministic OpenAI-compatible upstream for kairo issue 082.

The first turn returns a mixed agent-mode response. The consumer turn applies
one disclosed policy: request any side effect whose result marker is absent
from the conversation. Every received and emitted HTTP byte is frozen under
CAPTURE_DIR with the provider credential replaced by an inline marker.
"""

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ["PORT"])
CAPTURE_DIR = Path(os.environ["CAPTURE_DIR"])
MODEL = "mimo-v2.5"
SYNTHETIC_PROVIDER_KEY = "sk-kairo-" + "082-runtime-only"
LOCK = threading.Lock()
COUNTER = 0


def compact(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def effect_marker(operation, cell, run):
    return "KAIRO_082_EFFECT_%s_%s_%s" % (operation.upper(), cell, run)


def call_id(operation, cell, run, phase="initial"):
    return "call_082_%s_%s_%s_%s" % (phase, operation, cell, run)


def tool_call(operation, tool_name, cell, run, phase="initial"):
    return {
        "id": call_id(operation, cell, run, phase),
        "type": "function",
        "function": {
            "name": "KairoSideEffect-" + tool_name,
            "arguments": compact(
                {
                    "cell": cell,
                    "operation": operation,
                    "phase": phase,
                    "run": run,
                    "source_tool_call_id": call_id(
                        operation, cell, run, phase
                    ),
                }
            ),
        },
    }


def chat_response(message, finish_reason, response_id):
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": 1789934672,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
        },
    }


def parse_marker(raw, prefix):
    match = re.search(prefix + r" cell=([a-z_]+) run=([0-9]{2})", raw)
    if not match:
        return None
    return match.group(1), match.group(2)


def make_response(raw):
    consumer = parse_marker(raw, "KAIRO_082_CONSUMER")
    if consumer:
        cell, run = consumer
        expected = ["alpha", "beta"]
        missing = [
            operation
            for operation in expected
            if effect_marker(operation, cell, run) not in raw
        ]
        if missing:
            operation = missing[0]
            return chat_response(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        tool_call(operation, "charge", cell, run, "consumer")
                    ],
                },
                "tool_calls",
                "chatcmpl-082-consumer-retry-%s-%s" % (cell, run),
            )
        return chat_response(
            {
                "role": "assistant",
                "content": "KAIRO_082_CONSUMER_COMPLETE cell=%s run=%s"
                % (cell, run),
            },
            "stop",
            "chatcmpl-082-consumer-complete-%s-%s" % (cell, run),
        )

    first = parse_marker(raw, "KAIRO_082_FIRST")
    if not first:
        return chat_response(
            {"role": "assistant", "content": "KAIRO_082_UNMARKED_CONTROL"},
            "stop",
            "chatcmpl-082-unmarked",
        )

    cell, run = first
    if cell == "violation":
        calls = [
            tool_call("alpha", "charge", cell, run),
            tool_call("beta", "charge", cell, run),
        ]
    elif cell == "control_distinct":
        calls = [
            tool_call("alpha", "charge", cell, run),
            tool_call("beta", "credit", cell, run),
        ]
    elif cell == "control_single":
        calls = [tool_call("alpha", "charge", cell, run)]
    else:
        raise ValueError("unknown first-turn cell %r" % (cell,))

    calls.append(tool_call("review", "review", cell, run))
    return chat_response(
        {"role": "assistant", "content": None, "tool_calls": calls},
        "tool_calls",
        "chatcmpl-082-first-%s-%s" % (cell, run),
    )


def sanitized_headers(headers):
    result = []
    for name, value in headers.items():
        if name.lower() == "authorization":
            value = "Bearer <SYNTHETIC_PROVIDER_KEY>"
        else:
            value = value.replace(
                SYNTHETIC_PROVIDER_KEY, "<SYNTHETIC_PROVIDER_KEY>"
            )
        result.append((name, value))
    return result


def raw_request(method, target, headers, body):
    lines = ["%s %s HTTP/1.1" % (method, target)]
    lines.extend("%s: %s" % item for item in sanitized_headers(headers))
    return "\r\n".join(lines) + "\r\n\r\n" + body


def raw_response(status, headers, body):
    reason = {200: "OK", 404: "Not Found"}.get(status, "Unknown")
    lines = ["HTTP/1.1 %d %s" % (status, reason)]
    lines.extend("%s: %s" % item for item in headers)
    return "\r\n".join(lines) + "\r\n\r\n" + body


def freeze_exchange(method, target, request_headers, request_body, status, response_headers, response_body):
    global COUNTER
    with LOCK:
        COUNTER += 1
        sequence = COUNTER
        prefix = CAPTURE_DIR / ("upstream-%03d" % sequence)
        request_text = raw_request(method, target, request_headers, request_body)
        response_text = raw_response(status, response_headers, response_body)
        prefix.with_name(prefix.name + "-request.http").write_text(request_text)
        prefix.with_name(prefix.name + "-response.http").write_text(response_text)
        with (CAPTURE_DIR / "upstream.jsonl").open("a") as capture:
            capture.write(
                compact(
                    {
                        "request_file": prefix.name + "-request.http",
                        "response_file": prefix.name + "-response.http",
                        "sequence": sequence,
                        "target": target,
                    }
                )
                + "\n"
            )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def send_json(self, value, status=200):
        body = compact(value)
        response_headers = [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body.encode()))),
        ]
        freeze_exchange(
            self.command,
            self.path,
            self.headers,
            self.request_body,
            status,
            response_headers,
            body,
        )
        self.send_response(status)
        for name, value in response_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        self.request_body = ""
        if self.path.rstrip("/").endswith("/models"):
            self.send_json(
                {
                    "object": "list",
                    "data": [
                        {"id": MODEL, "object": "model", "owned_by": "kairo"}
                    ],
                }
            )
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        self.request_body = self.rfile.read(length).decode("utf-8", "replace")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self.send_json({"error": "not found"}, 404)
            return
        self.send_json(make_response(self.request_body))


if __name__ == "__main__":
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
