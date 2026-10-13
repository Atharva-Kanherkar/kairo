"""Deterministic OpenAI Chat upstream that streams one tool call with its arguments split into many deltas."""

import json

from kairo_verify import Reply

ARGS = {"path": "src/main.rs", "patch": "fn main() {\n    println!(\"hi \\\"there\\\"\");\n}\n", "unicode": "café → ok"}


def make_responder(args=ARGS, pieces=17):
    text = json.dumps(args)
    size = max(1, len(text) // pieces)
    parts = [text[i:i + size] for i in range(0, len(text), size)]

    def respond(cap):
        base = {"id": "chatcmpl-kairo", "object": "chat.completion.chunk", "created": 0, "model": "captured-model"}
        chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
            {"index": 0, "id": "call_split", "type": "function", "function": {"name": "apply_patch", "arguments": ""}}]}}]}]
        chunks += [{**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": p}}]}}]}
                   for p in parts]
        chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        return Reply.sse(chunks, done=True)
    return respond


def messages_body():
    return {"model": "captured-model", "max_tokens": 256, "stream": True, "messages": [{"role": "user", "content": "apply the patch"}],
            "tools": [{"name": "apply_patch", "description": "Apply a patch",
                       "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}]}
