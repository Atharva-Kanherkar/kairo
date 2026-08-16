#!/usr/bin/env python3
"""Capture-and-respond upstream for hunting Bifrost translation bugs.

Two jobs:

1. CAPTURE. Every request Bifrost forwards is appended to capture.jsonl as
   {"path", "headers", "body"}. Pointing a translator's backend at a capture
   server is how request-side (encode) losses are exposed offline: whatever the
   client sent that never appears in the capture was dropped in translation.

2. RESPOND. Returns a crafted response so the decode side can be probed too.
   The scenario is selected by a marker substring in the raw request body, so a
   single server serves every probe without restarts.

Speaks OpenAI Chat Completions and the Responses API, because Bifrost drives an
OpenAI-compatible upstream through /v1/responses on some client routes.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "9911"))
CAPTURE = os.environ.get("CAPTURE", "capture.jsonl")
MODEL = "mimo-v2.5"
TEXT = "I will check the time."

# ---------------------------------------------------------------- responses --

def cc(**kw):
    base = {"id": "chatcmpl-hunt", "object": "chat.completion", "model": MODEL,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    base.update(kw)
    return base


def cc_msg(message, finish="stop"):
    return cc(choices=[{"index": 0, "message": message, "finish_reason": finish}])


TOOLCALL_MSG = {"role": "assistant", "content": TEXT, "tool_calls": [
    {"id": "call_gt1", "type": "function",
     "function": {"name": "get_time", "arguments": "{}"}}]}

# Scenario table: marker -> (chat_completions_response, responses_api_response)
# Responses-API payloads are built by resp() below.

def resp(output, status="completed", extra=None):
    r = {"id": "resp_hunt", "object": "response", "status": status, "model": MODEL,
         "output": output,
         "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
    if extra:
        r.update(extra)
    return r


def out_text(t=TEXT):
    return {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
            "content": [{"type": "output_text", "text": t}]}


def out_fc(call_id="call_gt1", name="get_time", args="{}"):
    return {"type": "function_call", "id": "fc_1", "call_id": call_id, "name": name,
            "arguments": args, "status": "completed"}


# A tool-call id that is legal for OpenAI but violates the documented
# ^[a-zA-Z0-9_-]{1,64}$ charset contract Anthropic enforces on tool_use ids.
NASTY_ID = "call/with+punct=and.dots:1"
LONG_ID = "call_" + ("x" * 200)


def pick(body_raw):
    """Return (chat_response, responses_response) for the scenario in this body."""
    b = body_raw
    if "SCENARIO_CONTENT_FILTER" in b:
        return (cc_msg({"role": "assistant", "content": "blocked"}, finish="content_filter"),
                resp([out_text("blocked")], extra={"incomplete_details": {"reason": "content_filter"}}))
    if "SCENARIO_NASTY_TOOLID" in b:
        msg = {"role": "assistant", "content": None, "tool_calls": [
            {"id": NASTY_ID, "type": "function",
             "function": {"name": "get_time", "arguments": "{}"}}]}
        return (cc_msg(msg, finish="tool_calls"),
                resp([out_fc(call_id=NASTY_ID)]))
    if "SCENARIO_LONG_TOOLID" in b:
        msg = {"role": "assistant", "content": None, "tool_calls": [
            {"id": LONG_ID, "type": "function",
             "function": {"name": "get_time", "arguments": "{}"}}]}
        return (cc_msg(msg, finish="tool_calls"), resp([out_fc(call_id=LONG_ID)]))
    if "SCENARIO_WEIRD_FINISH" in b:
        return (cc_msg({"role": "assistant", "content": "x"}, finish="banana"),
                resp([out_text("x")], status="banana"))
    if "SCENARIO_LENGTH" in b:
        return (cc_msg({"role": "assistant", "content": "trunc"}, finish="length"),
                resp([out_text("trunc")], status="incomplete",
                     extra={"incomplete_details": {"reason": "max_output_tokens"}}))
    if "SCENARIO_REFUSAL" in b:
        return (cc_msg({"role": "assistant", "content": None, "refusal": "I cannot help"}),
                resp([{"type": "message", "id": "msg_1", "status": "completed",
                       "role": "assistant",
                       "content": [{"type": "refusal", "refusal": "I cannot help"}]}]))
    # default: text + tool call
    return (cc_msg(TOOLCALL_MSG, finish="tool_calls"),
            resp([out_text(), out_fc()]))


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
        rec = {"path": self.path,
               "headers": {k.lower(): v for k, v in self.headers.items()},
               "body": raw}
        with open(CAPTURE, "a") as f:
            f.write(json.dumps(rec) + "\n")
        chat, responses = pick(raw)
        is_responses = self.path.rstrip("/").endswith("/responses")
        self._json(responses if is_responses else chat)


if __name__ == "__main__":
    open(CAPTURE, "w").close()
    print("[capture] upstream on :%d -> %s" % (PORT, CAPTURE), file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
