"""Deterministic OpenAI Responses-API upstream for issue 085.

Every response is built server-side from the incoming JSON body's `model`
field alone (never from external state), so a rerun is reproducible. Primary
scenarios stream some output and then fail, either with a retriable in-band
event or at the transport layer. Fallback scenarios complete cleanly. See
CaptureServer/UpstreamHandler in reproduce.py for how each exchange is
recorded and sanitized.
"""

import itertools
import json

SEQ = itertools.count
ARGUMENTS = '{"line":"run"}'
SERVER_ERROR = {"type": "server_error", "code": "server_error", "message": "upstream error"}


def sse(event_type, **fields):
    payload = {"type": event_type, **fields}
    return f"event: {event_type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


def _response_obj(rid, model, status, output=None, error=None):
    return {
        "id": rid,
        "object": "response",
        "created_at": 0,
        "status": status,
        "model": model,
        "output": output or [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "error": error,
    }


def _function_call(seq, call_id, output_index, complete=True):
    """An `append_ledger` function_call item. With `complete=False` only the
    `response.output_item.added` announcement is streamed."""
    item = {
        "id": f"fc_{call_id}", "type": "function_call", "status": "in_progress",
        "name": "append_ledger", "call_id": call_id, "arguments": "",
    }
    events = [sse("response.output_item.added", sequence_number=next(seq), output_index=output_index, item=item)]
    if not complete:
        return events, None
    done = dict(item, status="completed", arguments=ARGUMENTS)
    events += [
        sse("response.function_call_arguments.delta", sequence_number=next(seq),
            output_index=output_index, item_id=item["id"], delta=ARGUMENTS),
        sse("response.function_call_arguments.done", sequence_number=next(seq),
            output_index=output_index, item_id=item["id"], arguments=ARGUMENTS),
        sse("response.output_item.done", sequence_number=next(seq), output_index=output_index, item=done),
    ]
    return events, done


def _reasoning(seq, item_id, output_index):
    """A reasoning item as a reasoning model streams it before its tool call."""
    item = {"id": item_id, "type": "reasoning", "summary": []}
    return [
        sse("response.output_item.added", sequence_number=next(seq), output_index=output_index, item=item),
        sse("response.output_item.done", sequence_number=next(seq), output_index=output_index, item=item),
    ], item


def _partial_text(seq):
    msg = {"id": "msg_partial", "type": "message", "status": "in_progress", "role": "assistant", "content": []}
    return [
        sse("response.output_item.added", sequence_number=next(seq), output_index=0, item=msg),
        sse("response.content_part.added", sequence_number=next(seq), output_index=0, content_index=0,
            item_id="msg_partial", part={"type": "output_text", "text": "", "annotations": []}),
        sse("response.output_text.delta", sequence_number=next(seq), output_index=0, content_index=0,
            item_id="msg_partial", delta="partial answer", logprobs=[]),
    ]


def _error_event(seq):
    """A retriable in-band `error` event (a 500 the client did not cause)."""
    return sse("error", sequence_number=next(seq), error=SERVER_ERROR)


def _failed_event(seq, rid, model):
    """The documented terminal failure event, with the same retriable code."""
    return sse("response.failed", sequence_number=next(seq),
               response=_response_obj(rid, model, "failed", error={"code": "server_error", "message": "upstream error"}))


def _completed(seq, rid, model, output):
    return sse("response.completed", sequence_number=next(seq), response=_response_obj(rid, model, "completed", output))


def drops_connection(model):
    """True for the one scenario that fails at the transport layer: the
    upstream closes the connection before its declared body length."""
    return model == "primary-tool-drop"


def provider_stream(request_body, call_counter):
    """Build the exact SSE bytes this deterministic upstream sends for one
    call, keyed only on the incoming JSON body's `model` field. `call_counter`
    disambiguates the response id across repeated calls in one trial."""
    model = request_body.get("model", "")
    rid = f"resp_{model}_{next(call_counter)}"
    seq = SEQ()
    out = [sse("response.created", sequence_number=next(seq), response=_response_obj(rid, model, "in_progress"))]
    if model in ("primary-tool", "primary-tool-failed", "primary-tool-drop"):
        events, _ = _function_call(seq, "call_primary", 0)
        out += events
        if model == "primary-tool":
            out.append(_error_event(seq))
        elif model == "primary-tool-failed":
            out.append(_failed_event(seq, rid, model))
    elif model == "primary-announced":
        events, _ = _function_call(seq, "call_primary", 0, complete=False)
        out += events + [_error_event(seq)]
    elif model == "primary-prefail":
        out.append(_error_event(seq))
    elif model == "primary-textpartial":
        out += _partial_text(seq) + [_error_event(seq)]
    elif model == "primary-nofault":
        events, done = _function_call(seq, "call_primary", 0)
        out += events + [_completed(seq, rid, model, [done])]
    elif model == "fallback-tool":
        events, done = _function_call(seq, "call_fallback", 0)
        out += events + [_completed(seq, rid, model, [done])]
    elif model == "fallback-reasoning-tool":
        reasoning_events, reasoning = _reasoning(seq, "rs_fallback", 0)
        call_events, done = _function_call(seq, "call_fallback", 1)
        out += reasoning_events + call_events + [_completed(seq, rid, model, [reasoning, done])]
    else:
        raise ValueError(f"unknown deterministic scenario model: {model!r}")
    return b"".join(out)
