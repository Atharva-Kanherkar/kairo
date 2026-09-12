#!/usr/bin/env python3
"""076 mock upstream: same safety turn over non-stream and stream.

The Anthropic route drives an OpenAI-compatible upstream through the Responses
API, so this mock speaks both dialects. The two scenarios use the EXACT same
non-stream response objects as transcripts/bifrost-rig/capture_upstream.py
(SCENARIO_CONTENT_FILTER and SCENARIO_PLAIN_TEXT), so any difference from the
034/035 findings is transport-only, not shape drift.

  /v1/responses (non-stream) -> full Responses JSON (filter or plain)
  /v1/responses (stream)     -> SSE frames ending in response.completed
                               carrying the SAME response object
  /v1/chat/completions       -> Chat JSON (finish content_filter or stop)
  /v1/models                 -> model list

Every request is appended to capture.jsonl as {"path","body","response"} so
the forwarded request is ground truth for encode-side checks.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "9921"))
CAPTURE = os.environ.get("CAPTURE", "capture.jsonl")
MODEL = "mimo-v2.5"


def cc(**kw):
    base = {"id": "chatcmpl-076", "object": "chat.completion", "model": MODEL,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    base.update(kw)
    return base


def cc_msg(message, finish="stop"):
    return cc(choices=[{"index": 0, "message": message, "finish_reason": finish}])


def resp(output, status="completed", extra=None):
    r = {"id": "resp_076", "object": "response", "status": status, "model": MODEL,
         "output": output,
         "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
    if extra:
        r.update(extra)
    return r


def out_text(t):
    return {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
            "content": [{"type": "output_text", "text": t}]}


# Non-stream response objects. Byte-identical in shape to the 034 rig:
# status "completed" WITH incomplete_details content_filter (contradictory on
# purpose, frozen by 034), and a plain complete turn as control.
RESP_FILTER = resp([out_text("blocked")], status="incomplete",
                   extra={"incomplete_details": {"reason": "content_filter"}})
RESP_PLAIN = resp([out_text("all done")])
CHAT_FILTER = cc_msg({"role": "assistant", "content": "blocked"}, finish="content_filter")
CHAT_PLAIN = cc_msg({"role": "assistant", "content": "all done"}, finish="stop")


def stream_frames(final):
    """SSE frames for a text-only turn ending in `final` (the completed object)."""
    text = final["output"][0]["content"][0]["text"]
    return [
        {"type": "response.created", "response": {"id": "resp_076", "object": "response",
         "status": "in_progress", "model": MODEL, "output": []}},
        {"type": "response.output_item.added", "output_index": 0, "item": {
            "type": "message", "id": "msg_1", "status": "in_progress",
            "role": "assistant", "content": []}},
        {"type": "response.content_part.added", "item_id": "msg_1", "output_index": 0,
         "content_index": 0, "part": {"type": "output_text", "text": ""}},
        {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0,
         "content_index": 0, "delta": text},
        {"type": "response.output_text.done", "item_id": "msg_1", "output_index": 0,
         "content_index": 0, "text": text},
        {"type": "response.content_part.done", "item_id": "msg_1", "output_index": 0,
         "content_index": 0, "part": {"type": "output_text", "text": text}},
        {"type": "response.output_item.done", "output_index": 0, "item": {
            "type": "message", "id": "msg_1", "status": "completed",
            "role": "assistant", "content": [{"type": "output_text", "text": text}]}},
        {"type": "response.completed", "response": final},
    ]


def pick(body_raw):
    b = body_raw
    if "SCENARIO_CONTENT_FILTER" in b:
        return CHAT_FILTER, RESP_FILTER
    return CHAT_PLAIN, RESP_PLAIN


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _sse(self, frames):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        for f in frames:
            self.wfile.write(("data: " + json.dumps(f) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json({"object": "list",
                        "data": [{"id": MODEL, "object": "model", "owned_by": "mock"}]})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(n).decode("utf-8", "replace") if n else "{}"
        chat, responses = pick(raw)
        is_responses = self.path.rstrip("/").endswith("/responses")
        try:
            req = json.loads(raw or "{}")
        except Exception:
            req = {}
        stream = bool(req.get("stream"))
        if is_responses:
            reply = responses
            if stream:
                rec = {"path": self.path, "body": raw, "response": responses,
                       "stream_frames": stream_frames(responses)}
                with open(CAPTURE, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                self._sse(stream_frames(responses))
                return
        else:
            reply = chat
        rec = {"path": self.path, "body": raw, "response": reply}
        with open(CAPTURE, "a") as f:
            f.write(json.dumps(rec) + "\n")
        self._json(reply)


if __name__ == "__main__":
    open(CAPTURE, "w").close()
    print("[mock076] upstream on :%d -> %s" % (PORT, CAPTURE), file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
