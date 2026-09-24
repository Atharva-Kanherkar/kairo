"""Deterministic OpenAI Responses-API upstream for issue 085.

Every response is built server-side from the incoming JSON body's `model`
field alone (never from external state), so a rerun is bit-for-bit
reproducible. Exposes exactly the scenarios reproduce.py's LiteLLM router
config maps its deployments to. See CaptureServer/UpstreamHandler in
reproduce.py for how each exchange is recorded and sanitized.
"""

import itertools
import json

SEQ = itertools.count


def sse(event_type, **fields):
    payload = {"type": event_type, **fields}
    return f"event: {event_type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


def _response_obj(rid, model, status, error=None):
    return {
        "id": rid,
        "object": "response",
        "created_at": 0,
        "status": status,
        "model": model,
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "error": error,
    }


def _tool_call_then_terminal(rid, model, call_id, fail):
    """response.created -> a completed function_call -> either response.completed
    or a mid-stream `error` event (a retriable 500 the client did not cause)."""
    seq = SEQ()
    out = [sse("response.created", sequence_number=next(seq), response=_response_obj(rid, model, "in_progress"))]
    item_in_progress = {
        "id": f"fc_{call_id}", "type": "function_call", "status": "in_progress",
        "name": "append_ledger", "call_id": call_id, "arguments": "",
    }
    out.append(sse("response.output_item.added", sequence_number=next(seq), output_index=0, item=item_in_progress))
    args = '{"line":"run"}'
    out.append(sse("response.function_call_arguments.delta", sequence_number=next(seq),
                    output_index=0, item_id=f"fc_{call_id}", delta=args))
    out.append(sse("response.function_call_arguments.done", sequence_number=next(seq),
                    output_index=0, item_id=f"fc_{call_id}", arguments=args))
    item_done = dict(item_in_progress, status="completed", arguments=args)
    out.append(sse("response.output_item.done", sequence_number=next(seq), output_index=0, item=item_done))
    if fail:
        out.append(sse("error", sequence_number=next(seq),
                        error={"type": "server_error", "code": "server_error", "message": "upstream error"}))
    else:
        completed = dict(_response_obj(rid, model, "completed"), output=[item_done])
        out.append(sse("response.completed", sequence_number=next(seq), response=completed))
    return b"".join(out)


def _immediate_error(rid, model):
    """A retriable failure before any output item is emitted."""
    seq = SEQ()
    out = [sse("response.created", sequence_number=next(seq), response=_response_obj(rid, model, "in_progress"))]
    out.append(sse("error", sequence_number=next(seq),
                    error={"type": "server_error", "code": "server_error", "message": "upstream error"}))
    return b"".join(out)


def _text_partial_then_error(rid, model):
    """Streams partial assistant text, then fails before response.completed."""
    seq = SEQ()
    out = [sse("response.created", sequence_number=next(seq), response=_response_obj(rid, model, "in_progress"))]
    msg = {"id": "msg_partial", "type": "message", "status": "in_progress", "role": "assistant", "content": []}
    out.append(sse("response.output_item.added", sequence_number=next(seq), output_index=0, item=msg))
    out.append(sse("response.content_part.added", sequence_number=next(seq), output_index=0, content_index=0,
                    item_id="msg_partial", part={"type": "output_text", "text": "", "annotations": []}))
    out.append(sse("response.output_text.delta", sequence_number=next(seq), output_index=0, content_index=0,
                    item_id="msg_partial", delta="partial answer", logprobs=[]))
    out.append(sse("error", sequence_number=next(seq),
                    error={"type": "server_error", "code": "server_error", "message": "upstream error"}))
    return b"".join(out)


def _text_message_complete(rid, model):
    """A clean completed response whose sole output item is an assistant
    text message (a different item type than the primary's function_call)."""
    seq = SEQ()
    out = [sse("response.created", sequence_number=next(seq), response=_response_obj(rid, model, "in_progress"))]
    msg = {"id": "msg_fallback", "type": "message", "status": "in_progress", "role": "assistant", "content": []}
    out.append(sse("response.output_item.added", sequence_number=next(seq), output_index=0, item=msg))
    out.append(sse("response.content_part.added", sequence_number=next(seq), output_index=0, content_index=0,
                    item_id="msg_fallback", part={"type": "output_text", "text": "", "annotations": []}))
    out.append(sse("response.output_text.delta", sequence_number=next(seq), output_index=0, content_index=0,
                    item_id="msg_fallback", delta="done", logprobs=[]))
    out.append(sse("response.output_text.done", sequence_number=next(seq), output_index=0, content_index=0,
                    item_id="msg_fallback", text="done", logprobs=[]))
    done = dict(msg, status="completed", content=[{"type": "output_text", "text": "done", "annotations": []}])
    out.append(sse("response.output_item.done", sequence_number=next(seq), output_index=0, item=done))
    completed = dict(_response_obj(rid, model, "completed"), output=[done])
    out.append(sse("response.completed", sequence_number=next(seq), response=completed))
    return b"".join(out)


def provider_stream(request_body, call_counter):
    """Build the exact SSE bytes this deterministic upstream sends for one
    call, keyed only on the incoming JSON body's `model` field. `call_counter`
    disambiguates the response id across repeated calls in one trial."""
    model = request_body.get("model", "")
    rid = f"resp_{model}_{next(call_counter)}"
    if model == "primary-nofault":
        return _tool_call_then_terminal(rid, model, "call_primary", fail=False)
    if model == "primary-prefail":
        return _immediate_error(rid, model)
    if model == "primary-textpartial":
        return _text_partial_then_error(rid, model)
    if model == "primary-tool":
        return _tool_call_then_terminal(rid, model, "call_primary", fail=True)
    if model == "fallback-tool":
        return _tool_call_then_terminal(rid, model, "call_fallback", fail=False)
    if model == "fallback-text":
        return _text_message_complete(rid, model)
    raise ValueError(f"unknown deterministic scenario model: {model!r}")
