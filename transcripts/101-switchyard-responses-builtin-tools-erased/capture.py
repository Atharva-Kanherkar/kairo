#!/usr/bin/env python3
"""Deterministic capture upstream for finding 101.

Records the exact bytes Switchyard forwards (path, headers, raw body) and
answers with a canned response for the dialect named in the request path.
Streaming is selected by the forwarded body's own `stream` flag, so the same
upstream serves both modes.

  /chat -> OpenAI Chat Completions   /anth -> Anthropic Messages
  /resp -> OpenAI Responses

No credentials, no network egress, no model. Every reply is a literal.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
OUT = sys.argv[2]

CHAT = {
    "id": "chatcmpl-cap", "object": "chat.completion", "created": 0,
    "model": "captured-model",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "CANARY_ANSWER"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
}

ANTHROPIC = {
    "id": "msg_cap", "type": "message", "role": "assistant",
    "model": "captured-model",
    "content": [{"type": "text", "text": "CANARY_ANSWER"}],
    "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 11, "output_tokens": 3},
}

RESPONSES = {
    "id": "resp_cap", "object": "response", "created_at": 0,
    "model": "captured-model", "status": "completed",
    "output": [{"type": "message", "id": "msg_cap_1", "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "CANARY_ANSWER",
                             "annotations": []}]}],
    "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
    "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
}

CHAT_SSE = b"".join([
    b"data: " + json.dumps({"id": "chatcmpl-cap", "object": "chat.completion.chunk",
                            "created": 0, "model": "captured-model",
                            "choices": [{"index": 0, "delta": {"role": "assistant",
                                                               "content": "CANARY_ANSWER"},
                                         "finish_reason": None}]}).encode() + b"\n\n",
    b"data: " + json.dumps({"id": "chatcmpl-cap", "object": "chat.completion.chunk",
                            "created": 0, "model": "captured-model",
                            "choices": [{"index": 0, "delta": {},
                                         "finish_reason": "stop"}]}).encode() + b"\n\n",
    b"data: [DONE]\n\n",
])

ANTHROPIC_SSE = b"".join([
    b"event: message_start\ndata: " + json.dumps({
        "type": "message_start",
        "message": {"id": "msg_cap", "type": "message", "role": "assistant",
                    "model": "captured-model", "content": [],
                    "stop_reason": None, "stop_sequence": None,
                    "usage": {"input_tokens": 11, "output_tokens": 1}}}).encode() + b"\n\n",
    b"event: content_block_start\ndata: " + json.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""}}).encode() + b"\n\n",
    b"event: content_block_delta\ndata: " + json.dumps({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "CANARY_ANSWER"}}).encode() + b"\n\n",
    b"event: content_block_stop\ndata: " + json.dumps({
        "type": "content_block_stop", "index": 0}).encode() + b"\n\n",
    b"event: message_delta\ndata: " + json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 3}}).encode() + b"\n\n",
    b"event: message_stop\ndata: " + json.dumps({"type": "message_stop"}).encode() + b"\n\n",
])


def canned(path):
    if path.startswith("/chat"):
        return CHAT
    if path.startswith("/anth"):
        return ANTHROPIC
    return RESPONSES


def sse_for(path):
    if path.startswith("/chat"):
        return CHAT_SSE
    if path.startswith("/anth"):
        return ANTHROPIC_SSE
    return b"data: " + json.dumps(RESPONSES).encode() + b"\n\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            stream = bool(json.loads(raw or b"{}").get("stream"))
        except Exception:
            stream = False
        with open(OUT, "a") as handle:
            handle.write(json.dumps({
                "path": self.path,
                "method": "POST",
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "raw": raw.decode("utf-8", "replace"),
            }) + "\n")
        if stream:
            payload = sse_for(self.path)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = json.dumps(canned(self.path)).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()