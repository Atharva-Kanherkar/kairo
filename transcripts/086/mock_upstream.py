#!/usr/bin/env python3
"""Deterministic OpenAI-compatible upstream for kairo issue 086.

Serves Chat Completions and the Responses API, streaming and non-streaming.
Every answer names the endpoint that produced it and carries a sequence
number, so a client can tell which upstream object it was served. Every
received and emitted HTTP byte is frozen under CAPTURE_DIR with the provider
credential replaced by an inline marker.
"""

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ["PORT"])
CAPTURE_DIR = Path(os.environ["CAPTURE_DIR"])
MODEL = "gpt-4o-mini"
SYNTHETIC_PROVIDER_KEY = "sk-kairo-" + "086-runtime-only"
LOCK = threading.Lock()
COUNTER = 0


def compact(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def run_marker(raw):
    match = re.search(r"KAIRO_086 cell=([a-z_]+) run=([0-9]{2})", raw, re.I)
    if not match:
        return "unmarked", "00"
    return match.group(1).lower(), match.group(2)


def chat_object(sequence, cell, run):
    text = "KAIRO_086_UPSTREAM_CHAT seq=%03d cell=%s run=%s" % (sequence, cell, run)
    return {
        "id": "chatcmpl-086-%03d" % sequence,
        "object": "chat.completion",
        "created": 1790000000,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }


def chat_stream(sequence, cell, run):
    text = "KAIRO_086_UPSTREAM_CHAT seq=%03d cell=%s run=%s" % (sequence, cell, run)
    base = {
        "id": "chatcmpl-086-%03d" % sequence,
        "object": "chat.completion.chunk",
        "created": 1790000000,
        "model": MODEL,
    }
    chunks = [
        dict(base, choices=[{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]),
        dict(base, choices=[{"index": 0, "delta": {"content": text}, "finish_reason": None}]),
        dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]),
        dict(
            base,
            choices=[],
            usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        ),
    ]
    body = "".join("data: %s\n\n" % compact(chunk) for chunk in chunks)
    return body + "data: [DONE]\n\n"


def responses_object(sequence, cell, run):
    text = "KAIRO_086_UPSTREAM_RESPONSES seq=%03d cell=%s run=%s" % (
        sequence,
        cell,
        run,
    )
    return {
        "id": "resp_086_%03d" % sequence,
        "object": "response",
        "created_at": 1790000000,
        "status": "completed",
        "model": MODEL,
        "output": [
            {
                "id": "msg_086_%03d" % sequence,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
    }


def responses_stream(sequence, cell, run):
    final = responses_object(sequence, cell, run)
    item = final["output"][0]
    text = item["content"][0]["text"]
    in_progress = dict(final, status="in_progress", output=[])
    events = [
        ("response.created", {"response": in_progress}),
        ("response.in_progress", {"response": in_progress}),
        (
            "response.output_item.added",
            {"output_index": 0, "item": dict(item, status="in_progress", content=[])},
        ),
        (
            "response.content_part.added",
            {
                "output_index": 0,
                "item_id": item["id"],
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        ),
        (
            "response.output_text.delta",
            {"output_index": 0, "item_id": item["id"], "content_index": 0, "delta": text},
        ),
        (
            "response.output_text.done",
            {"output_index": 0, "item_id": item["id"], "content_index": 0, "text": text},
        ),
        (
            "response.content_part.done",
            {
                "output_index": 0,
                "item_id": item["id"],
                "content_index": 0,
                "part": item["content"][0],
            },
        ),
        ("response.output_item.done", {"output_index": 0, "item": item}),
        ("response.completed", {"response": final}),
    ]
    body = ""
    for number, (event_type, payload) in enumerate(events):
        payload = dict(payload, type=event_type, sequence_number=number)
        body += "event: %s\ndata: %s\n\n" % (event_type, compact(payload))
    return body


def sanitized_headers(headers):
    result = []
    for name, value in headers.items():
        if name.lower() == "authorization":
            value = "Bearer <SYNTHETIC_PROVIDER_KEY>"
        else:
            value = value.replace(SYNTHETIC_PROVIDER_KEY, "<SYNTHETIC_PROVIDER_KEY>")
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


def next_sequence():
    global COUNTER
    with LOCK:
        COUNTER += 1
        return COUNTER


def freeze_exchange(sequence, method, target, request_headers, request_body, status, response_headers, response_body):
    cell, run = run_marker(request_body)
    prefix = "upstream-%03d" % sequence
    with LOCK:
        (CAPTURE_DIR / (prefix + "-request.http")).write_text(
            raw_request(method, target, request_headers, request_body)
        )
        (CAPTURE_DIR / (prefix + "-response.http")).write_text(
            raw_response(status, response_headers, response_body)
        )
        with (CAPTURE_DIR / "upstream.jsonl").open("a") as capture:
            capture.write(
                compact(
                    {
                        "cell": cell,
                        "request_file": prefix + "-request.http",
                        "response_file": prefix + "-response.http",
                        "run": run,
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

    def reply(self, status, content_type, body):
        sequence = self.sequence
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body.encode()))),
        ]
        freeze_exchange(
            sequence,
            self.command,
            self.path,
            self.headers,
            self.request_body,
            status,
            headers,
            body,
        )
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        self.request_body = ""
        self.sequence = next_sequence()
        if self.path.rstrip("/").endswith("/models"):
            self.reply(
                200,
                "application/json",
                compact({"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "kairo"}]}),
            )
        else:
            self.reply(404, "application/json", compact({"error": "not found"}))

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        self.request_body = self.rfile.read(length).decode("utf-8", "replace")
        self.sequence = next_sequence()
        try:
            payload = json.loads(self.request_body or "{}")
        except ValueError:
            payload = {}
        stream = bool(payload.get("stream"))
        cell, run = run_marker(self.request_body)
        path = self.path.rstrip("/")
        if path.endswith("/chat/completions"):
            if stream:
                self.reply(200, "text/event-stream", chat_stream(self.sequence, cell, run))
            else:
                self.reply(200, "application/json", compact(chat_object(self.sequence, cell, run)))
        elif path.endswith("/responses"):
            if stream:
                self.reply(200, "text/event-stream", responses_stream(self.sequence, cell, run))
            else:
                self.reply(200, "application/json", compact(responses_object(self.sequence, cell, run)))
        else:
            self.reply(404, "application/json", compact({"error": "not found"}))


if __name__ == "__main__":
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
