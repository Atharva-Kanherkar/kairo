#!/usr/bin/env python3
"""OpenAI-compatible upstream for reproducing Bifrost issue #6123.

Bifrost v1.6.11 drives an OpenAI upstream through the *Responses API* when the
client uses the /anthropic/v1/messages route, so this mock speaks BOTH dialects:

  /v1/responses         -> Responses API (text output + function_call output)
  /v1/chat/completions  -> Chat Completions (text + tool_call, finish_reason tool_calls)
  /v1/models            -> model list

Every response is the same logical turn: a short sentence THEN a get_time tool
call. In Chat Completions that means finish_reason "tool_calls"; in Responses that
means an output array whose last item is a function_call. Both should convert to an
Anthropic turn ending in stop_reason "tool_use".
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9911
MODEL = "mimo-v2.5"
TEXT = "I will check the time."

# ---- Chat Completions shapes ----
CC_STREAM = [
    {"choices": [{"index": 0, "delta": {"role": "assistant", "content": TEXT}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_gt1", "type": "function", "function": {"name": "get_time", "arguments": ""}}]}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32}},
]
CC_NONSTREAM = {
    "id": "chatcmpl-repro6123", "object": "chat.completion", "model": MODEL,
    "choices": [{"index": 0, "message": {"role": "assistant", "content": TEXT,
        "tool_calls": [{"id": "call_gt1", "type": "function", "function": {"name": "get_time", "arguments": "{}"}}]},
        "finish_reason": "tool_calls"}],
    "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
}

# ---- Responses API shapes ----
RESP_FINAL = {
    "id": "resp_repro6123", "object": "response", "status": "completed", "model": MODEL,
    "output": [
        {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
         "content": [{"type": "output_text", "text": TEXT}]},
        {"type": "function_call", "id": "fc_1", "call_id": "call_gt1", "name": "get_time",
         "arguments": "{}", "status": "completed"},
    ],
    "usage": {"input_tokens": 20, "output_tokens": 12, "total_tokens": 32},
}
RESP_STREAM = [
    {"type": "response.created", "response": {"id": "resp_repro6123", "object": "response", "status": "in_progress", "model": MODEL, "output": []}},
    {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "msg_1", "status": "in_progress", "role": "assistant", "content": []}},
    {"type": "response.content_part.added", "item_id": "msg_1", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": ""}},
    {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": TEXT},
    {"type": "response.output_text.done", "item_id": "msg_1", "output_index": 0, "content_index": 0, "text": TEXT},
    {"type": "response.content_part.done", "item_id": "msg_1", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": TEXT}},
    {"type": "response.output_item.done", "output_index": 0, "item": {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": TEXT}]}},
    {"type": "response.output_item.added", "output_index": 1, "item": {"type": "function_call", "id": "fc_1", "call_id": "call_gt1", "name": "get_time", "arguments": "", "status": "in_progress"}},
    {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "output_index": 1, "delta": "{}"},
    {"type": "response.function_call_arguments.done", "item_id": "fc_1", "output_index": 1, "arguments": "{}"},
    {"type": "response.output_item.done", "output_index": 1, "item": {"type": "function_call", "id": "fc_1", "call_id": "call_gt1", "name": "get_time", "arguments": "{}", "status": "completed"}},
    {"type": "response.completed", "response": RESP_FINAL},
]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        sys.stderr.write("[mock] %s %s\n" % (self.command, self.path)); sys.stderr.flush()

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _sse(self, frames):
        self.send_response(200); self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache"); self.end_headers()
        for f in frames:
            self.wfile.write(("data: " + json.dumps(f) + "\n\n").encode()); self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json({"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "mock"}]})
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except Exception:
            req = {}
        stream = bool(req.get("stream"))
        is_responses = self.path.rstrip("/").endswith("/responses")
        if is_responses:
            if stream:
                frames = [dict(f) for f in RESP_STREAM]
                self._sse(frames)
            else:
                self._json(RESP_FINAL)
        else:  # chat completions
            if stream:
                self._sse([{"id": "chatcmpl-repro6123", "object": "chat.completion.chunk", "model": MODEL, **c} for c in CC_STREAM])
            else:
                self._json(CC_NONSTREAM)


if __name__ == "__main__":
    print("[mock] OpenAI-compatible upstream (chat + responses) on :%d" % PORT, file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
