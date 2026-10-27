#!/usr/bin/env python3
"""Isolate the exact SSE byte that breaks the openai-python stream accumulator.

Serves hand-built OpenAI-shaped streams that differ from agentgateway's frozen
output in exactly one field, so the SDK's behaviour is decided by the bytes and
not by the gateway. No agentgateway in this loop.

Usage: isolate.py PORT
Then point the SDK at http://127.0.0.1:PORT/v1 and read which variant is served.
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_ID = "chatcmpl-iso"
CREATED = 1790518943


def chunk(delta, finish=None, usage=None):
    choices = []
    if delta is not None or finish is not None:
        c = {"index": 0, "delta": delta if delta is not None else {}}
        if finish is not None:
            c["finish_reason"] = finish
        choices.append(c)
    obj = {
        "id": BASE_ID,
        "choices": choices,
        "created": CREATED,
        "model": "captured-model",
        "service_tier": None,
        "system_fingerprint": None,
        "object": "chat.completion.chunk",
        "usage": usage,
    }
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n"


def variant(name):
    """Four variants of one tool-call stream. Only the continuation-delta
    `type` and `id` treatment differs."""
    out = []
    if name == "agw_verbatim":
        # exactly what agentgateway emits
        out.append(chunk({"content": None, "tool_calls": [
            {"index": 0, "id": "toolu_01", "type": "function",
             "function": {"name": "get_weather"}}], "role": "assistant"}))
        for frag in ('{"ci', 'ty":"sf"}'):
            out.append(chunk({"content": None, "tool_calls": [
                {"index": 0, "id": None, "type": None,
                 "function": {"arguments": frag}}]}))
    elif name == "type_omitted":
        # real OpenAI: continuation deltas carry only index + arguments
        out.append(chunk({"content": None, "tool_calls": [
            {"index": 0, "id": "toolu_01", "type": "function",
             "function": {"name": "get_weather"}}], "role": "assistant"}))
        for frag in ('{"ci', 'ty":"sf"}'):
            out.append(chunk({"content": None, "tool_calls": [
                {"index": 0, "function": {"arguments": frag}}]}))
    elif name == "type_repeated":
        # explicit "function" on every continuation delta
        out.append(chunk({"content": None, "tool_calls": [
            {"index": 0, "id": "toolu_01", "type": "function",
             "function": {"name": "get_weather"}}], "role": "assistant"}))
        for frag in ('{"ci', 'ty":"sf"}'):
            out.append(chunk({"content": None, "tool_calls": [
                {"index": 0, "id": None, "type": "function",
                 "function": {"arguments": frag}}]}))
    elif name == "type_null_only":
        # isolate `type` from `id`: keep id:null, drop type
        out.append(chunk({"content": None, "tool_calls": [
            {"index": 0, "id": "toolu_01", "type": "function",
             "function": {"name": "get_weather"}}], "role": "assistant"}))
        for frag in ('{"ci', 'ty":"sf"}'):
            out.append(chunk({"content": None, "tool_calls": [
                {"index": 0, "id": None, "function": {"arguments": frag}}]}))
    else:
        raise SystemExit(f"unknown variant {name}")

    out.append(chunk({"content": None}, finish="tool_calls",
                     usage={"prompt_tokens": 10, "completion_tokens": 5,
                            "total_tokens": 15}))
    out.append("data: [DONE]\n\n")
    return "".join(out)


VARIANTS = ["agw_verbatim", "type_omitted", "type_repeated", "type_null_only"]


def main():
    port = int(sys.argv[1])
    state = {"variant": "agw_verbatim", "hits": 0}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("content-length", 0) or 0)
            raw = self.rfile.read(n) if n else b""
            if self.path.endswith("/__variant"):
                # apply the requested variant; without this the server keeps
                # serving the initial variant and every "variant" is a lie.
                state.update(json.loads(raw))
                body = json.dumps(state).encode()
                ctype = "application/json"
            else:
                state["hits"] += 1
                body = variant(state["variant"]).encode()
                ctype = "text/event-stream"
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    if len(sys.argv) > 2:
        state["variant"] = sys.argv[2]
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    print(f"isolate server on 127.0.0.1:{port} variant={state['variant']}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
