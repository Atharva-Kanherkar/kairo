"""Deterministic OpenAI Responses upstream keyed on markers in the prompt.

TEXT_THEN_TOOL  text sentence, then a get_time function call (no explicit stop reason)
TOOL_ONLY       a get_time function call only
TRUNCATE        partial text, status incomplete, incomplete_details.reason max_output_tokens
FILTER          partial text, status incomplete, incomplete_details.reason content_filter
REFUSE          a message whose only content part is a structured refusal
anything else   plain text
`"stream": true` gets the equivalent Responses SSE event sequence.
"""

import json

from kairo_verify import Reply

MARKERS = ("TEXT_THEN_TOOL", "TOOL_ONLY", "TRUNCATE", "FILTER", "REFUSE")


def _resp(status, output, incomplete=None):
    r = {"id": "resp_kairo", "object": "response", "created_at": 0, "model": "gpt-4o", "status": status,
         "output": output, "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}}
    if incomplete:
        r["incomplete_details"] = {"reason": incomplete}
    return r


def _msg(part, status="completed"):
    return {"type": "message", "id": "msg_1", "status": status, "role": "assistant", "content": [part]}


def _text(text):
    return {"type": "output_text", "text": text, "annotations": []}


def _call(call_id="call_t1"):
    return {"type": "function_call", "id": "fc_1", "call_id": call_id, "name": "get_time", "arguments": '{"zone":"UTC"}',
            "status": "completed"}


def scenario(prompt, texts):
    if "TEXT_THEN_TOOL" in prompt:
        return [_msg(_text(texts["lead"])), _call()], "completed", None
    if "TOOL_ONLY" in prompt:
        return [_call()], "completed", None
    if "TRUNCATE" in prompt:
        return [_msg(_text(texts["partial"]), "incomplete")], "incomplete", "max_output_tokens"
    if "FILTER" in prompt:
        return [_msg(_text(texts["blocked"]), "incomplete")], "incomplete", "content_filter"
    if "REFUSE" in prompt:
        return [_msg({"type": "refusal", "refusal": texts["refusal"]})], "completed", None
    return [_msg(_text(texts["plain"]))], "completed", None


def _ev(kind, **f):
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **f}, separators=(',', ':'))}\n\n"


def _stream(output, status, incomplete):
    seq = iter(range(1000))
    frames = [_ev("response.created", sequence_number=next(seq), response=_resp("in_progress", []))]
    for idx, item in enumerate(output):
        if item["type"] == "function_call":
            frames += [
                _ev("response.output_item.added", sequence_number=next(seq), output_index=idx, item={**item, "arguments": "", "status": "in_progress"}),
                _ev("response.function_call_arguments.delta", sequence_number=next(seq), output_index=idx, item_id=item["id"], delta=item["arguments"]),
                _ev("response.function_call_arguments.done", sequence_number=next(seq), output_index=idx, item_id=item["id"], arguments=item["arguments"]),
                _ev("response.output_item.done", sequence_number=next(seq), output_index=idx, item=item),
            ]
            continue
        part = item["content"][0]
        refusal = part["type"] == "refusal"
        value = part["refusal"] if refusal else part["text"]
        empty = {"type": "refusal", "refusal": ""} if refusal else _text("")
        frames += [
            _ev("response.output_item.added", sequence_number=next(seq), output_index=idx, item={**item, "status": "in_progress", "content": []}),
            _ev("response.content_part.added", sequence_number=next(seq), output_index=idx, content_index=0, item_id=item["id"], part=empty),
            _ev("response.refusal.delta" if refusal else "response.output_text.delta", sequence_number=next(seq), output_index=idx, content_index=0, item_id=item["id"], delta=value),
            _ev("response.refusal.done" if refusal else "response.output_text.done", sequence_number=next(seq), output_index=idx, content_index=0, item_id=item["id"], **({"refusal": value} if refusal else {"text": value})),
            _ev("response.content_part.done", sequence_number=next(seq), output_index=idx, content_index=0, item_id=item["id"], part=part),
            _ev("response.output_item.done", sequence_number=next(seq), output_index=idx, item=item),
        ]
    terminal = "response.incomplete" if status == "incomplete" else "response.completed"
    frames.append(_ev(terminal, sequence_number=next(seq), response=_resp(status, output, incomplete)))
    return Reply(200, {"Content-Type": "text/event-stream"}, "".join(frames).encode())


def make_responder(**texts):
    t = {"lead": "Let me check the time.", "partial": "The first three steps are", "blocked": "I can",
         "refusal": "I can't help with that.", "plain": "Hello there.", **texts}

    def respond(cap):
        if cap.method == "GET":
            return Reply.json({"object": "list", "data": [{"id": "gpt-4o", "object": "model"}]})
        body = cap.json or {}
        output, status, incomplete = scenario(json.dumps(body.get("input")), t)
        if body.get("stream"):
            return _stream(output, status, incomplete)
        return Reply.json(_resp(status, output, incomplete))
    return respond


TOOLS = [{"name": "get_time", "description": "Current time in a zone",
          "input_schema": {"type": "object", "properties": {"zone": {"type": "string"}}, "required": ["zone"]}}]


def messages_body(prompt, stream=False, tools=True):
    body = {"model": "openai/gpt-4o", "max_tokens": 128, "messages": [{"role": "user", "content": prompt}]}
    if tools:
        body["tools"] = TOOLS
    if stream:
        body["stream"] = True
    return body
