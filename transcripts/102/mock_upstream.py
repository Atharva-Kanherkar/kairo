#!/usr/bin/env python3
"""Deterministic OpenAI Chat Completions backend standing in for the model.

It does three things:

1. records every forwarded request body verbatim as wire evidence,
2. reads the tool names the gateway actually offered upstream,
3. calls exactly one of those offered names, chosen by a canary in the last
   user message, so the reply is a legal tool call rather than an invented one.

The canary never names a tool that was not offered. If the gateway renames or
drops a tool, the call raises instead of guessing, so the rig cannot manufacture
a disagreement of its own.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Canary -> bare tool name the "model" picks when the gateway offered that name.
# Real Codex spells an MCP container "mcp__<server>__" with the trailing
# separator, so the flattened upstream name carries four underscores.
NAMESPACE = "mcp__docs__"
PICKS = {
    "PICK_LOCAL": "search",
    "PICK_QUALIFIED": NAMESPACE + "__search",
}


def _flatten(tools):
    """Codex nests tools in `namespace` containers; upstream gets them flat."""
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "namespace" and isinstance(tool.get("tools"), list):
            yield from _flatten(tool["tools"])
        elif isinstance(tool.get("function"), dict):
            names = [tool["function"].get("name")]
        else:
            names = [tool.get("name")]
        for name in names:
            if name:
                yield name


def offered_names(body):
    tools = body.get("tools")
    if not isinstance(tools, list):
        return []
    return list(_flatten(tools))


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def last_user_text(body):
    # Chat Completions carries `messages`; Responses carries `input`.
    messages = body.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if message.get("role") == "user":
                text = _text_of(message.get("content"))
                if text:
                    return text
    items = body.get("input")
    if isinstance(items, str):
        return items
    if isinstance(items, list):
        for item in reversed(items):
            text = _text_of(item.get("content"))
            if text:
                return text
    return ""


def pick_name(body):
    text = last_user_text(body)
    for canary, wanted in PICKS.items():
        if canary in text:
            names = offered_names(body)
            if wanted not in names:
                raise RuntimeError(
                    f"rig error: model was asked to call {wanted!r} "
                    f"but the gateway offered {names!r}"
                )
            return wanted
    raise RuntimeError(f"rig error: no pick canary in {text!r}")


def responses_body(base_id, name, arguments, model):
    return {
        "id": base_id,
        "object": "response",
        "created_at": 1,
        "model": model,
        "status": "completed",
        "output": [{
            "type": "function_call",
            "id": f"fc_{base_id}_0",
            "call_id": "call_local_1",
            "name": name,
            "arguments": arguments,
            "status": "completed",
        }],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--port", type=int, default=19321)
    parser.add_argument(
        "--dialect", choices=("chat", "responses"), default="chat",
        help="wire dialect the client speaks; controls the reply shape",
    )
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length", "0")))
            body = json.loads(raw)
            name = pick_name(body)
            with open(args.capture, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "path": self.path,
                    "tool_names_offered_upstream": offered_names(body),
                    "called_upstream_name": name,
                    "body_raw": raw.decode(),
                }, separators=(",", ":")) + "\n")

            response = {
                "id": f"chatcmpl-{name}",
                "object": "chat.completion",
                "created": 1,
                "model": body.get("model", "model/chat"),
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_local_1",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps({"q": "rust"}),
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

            if args.dialect == "responses":
                arguments = json.dumps({"q": "rust"})
                payload = responses_body(
                    f"resp-{name}", name, arguments, body.get("model", "model/chat"))
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    item = payload["output"][0]
                    frames = [
                        {"type": "response.created", "response": payload},
                        {"type": "response.output_item.added", "output_index": 0,
                         "item": {**item, "arguments": "", "status": "in_progress"}},
                        {"type": "response.function_call_arguments.delta",
                         "output_index": 0, "item_id": item["id"], "delta": arguments},
                        {"type": "response.function_call_arguments.done",
                         "output_index": 0, "item_id": item["id"], **item},
                        {"type": "response.output_item.done", "output_index": 0, "item": item},
                        {"type": "response.completed", "response": payload},
                    ]
                    for frame in frames:
                        event = frame["type"]
                        encoded_frame = json.dumps(frame, separators=(",", ":")).encode()
                        self.wfile.write(
                            b"event: " + event.encode() + b"\ndata: " + encoded_frame + b"\n\n")
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                encoded = json.dumps(payload, separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return

            if body.get("stream"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                base = {"id": response["id"], "object": "chat.completion.chunk",
                        "created": 1, "model": response["model"]}
                frames = [
                    {**base, "choices": [{"index": 0, "delta": {
                        "role": "assistant", "tool_calls": [{
                            "index": 0, "id": "call_local_1", "type": "function",
                            "function": {"name": name, "arguments": ""}}]}}]},
                    {**base, "choices": [{"index": 0, "delta": {"tool_calls": [
                        {"index": 0, "function": {"arguments": json.dumps({"q": "rust"})}}]}}]},
                    {**base, "choices": [{"index": 0, "delta": {},
                                          "finish_reason": "tool_calls"}],
                     "usage": response["usage"]},
                ]
                for frame in frames:
                    self.wfile.write(
                        b"data: " + json.dumps(frame, separators=(",", ":")).encode() + b"\n\n")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return

            encoded = json.dumps(response, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_):
            pass

    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()