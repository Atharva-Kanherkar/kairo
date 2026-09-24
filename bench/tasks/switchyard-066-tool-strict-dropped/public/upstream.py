"""Deterministic OpenAI Chat Completions upstream keyed on a marker in the last user message.

FILTER      finish_reason content_filter (partial text "I can")
MIXEDCHUNK  stream: one chunk carries reasoning_content and content together
TOOL        one lookup tool call (finish_reason tool_calls)
anything else  plain text "ok"
"""

import json

from kairo_verify import Reply

MODEL = "captured-model"


def _chat(message, finish):
    return {"id": "chatcmpl-kairo", "object": "chat.completion", "created": 0, "model": MODEL,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}


def _chunk(delta, finish=None):
    return {"id": "chatcmpl-kairo", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def make_responder(reasoning="Thinking it through.", answer="The answer is 4.", filtered="I can"):
    def respond(cap):
        body = cap.json or {}
        last = json.dumps((body.get("messages") or [{}])[-1])
        stream = bool(body.get("stream"))
        if "FILTER" in last:
            if stream:
                return Reply.sse([_chunk({"role": "assistant", "content": filtered}), _chunk({}, "content_filter")], done=True)
            return Reply.json(_chat({"role": "assistant", "content": filtered}, "content_filter"))
        if "MIXEDCHUNK" in last:
            if stream:
                return Reply.sse([_chunk({"role": "assistant", "reasoning_content": reasoning, "content": answer}),
                                  _chunk({}, "stop")], done=True)
            return Reply.json(_chat({"role": "assistant", "reasoning_content": reasoning, "content": answer}, "stop"))
        if "TOOL" in last:
            call = {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
            return Reply.json(_chat({"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"))
        if stream:
            return Reply.sse([_chunk({"role": "assistant", "content": "ok"}), _chunk({}, "stop")], done=True)
        return Reply.json(_chat({"role": "assistant", "content": "ok"}, "stop"))
    return respond


def messages_body(content, stream=False, **extra):
    body = {"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": content}], **extra}
    if stream:
        body["stream"] = True
    return body
