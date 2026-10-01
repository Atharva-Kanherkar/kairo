"""Deterministic OpenAI Responses upstream for mid-stream fallback scenarios.

Every stream is built from the request's `model` field alone:

  primary-tool         completed function call, then an in-band `error` event
  primary-tool-failed  completed function call, then `response.failed`
  primary-tool-drop    completed function call, then the connection drops
  primary-prefail      `response.created`, then an in-band `error` (no output)
  primary-nofault      completed function call, then `response.completed`
  fallback-tool        completed function call, then `response.completed`
  fallback-reasoning-tool  reasoning item, function call, `response.completed`
"""

import itertools
import json

from kairo_verify import Reply

ARGUMENTS = '{"line":"run"}'
SERVER_ERROR = {"type": "server_error", "code": "server_error", "message": "upstream error"}
SCENARIOS = {  # public model name -> (primary deployment, fallback deployment)
    "agent-error": ("primary-tool", "fallback-tool"),
    "agent-failed": ("primary-tool-failed", "fallback-tool"),
    "agent-reasoning": ("primary-tool", "fallback-reasoning-tool"),
    "agent-prefail": ("primary-prefail", "fallback-tool"),
    "agent-nofault": ("primary-nofault", "fallback-tool"),
    "agent-drop": ("primary-tool-drop", "fallback-tool"),
}
TOOLS = [{"type": "function", "name": "append_ledger", "description": "Append one line to the ledger. Has a side effect.",
          "parameters": {"type": "object", "properties": {"line": {"type": "string"}}, "required": ["line"],
                         "additionalProperties": False}, "strict": True}]


def _sse(event_type, **fields):
    return f"event: {event_type}\ndata: {json.dumps({'type': event_type, **fields}, separators=(',', ':'))}\n\n".encode()


def _resp(rid, model, status, output=None, error=None):
    return {"id": rid, "object": "response", "created_at": 0, "status": status, "model": model,
            "output": output or [], "parallel_tool_calls": True, "tool_choice": "auto", "tools": [], "error": error}


def _function_call(seq, call_id, index):
    item = {"id": f"fc_{call_id}", "type": "function_call", "status": "in_progress", "name": "append_ledger",
            "call_id": call_id, "arguments": ""}
    done = dict(item, status="completed", arguments=ARGUMENTS)
    return [
        _sse("response.output_item.added", sequence_number=next(seq), output_index=index, item=item),
        _sse("response.function_call_arguments.delta", sequence_number=next(seq), output_index=index, item_id=item["id"], delta=ARGUMENTS),
        _sse("response.function_call_arguments.done", sequence_number=next(seq), output_index=index, item_id=item["id"], arguments=ARGUMENTS),
        _sse("response.output_item.done", sequence_number=next(seq), output_index=index, item=done),
    ], done


def make_responder():
    counter = itertools.count(1)

    def respond(cap):
        if cap.method == "GET":
            return Reply.json({"object": "list", "data": [{"id": "mock", "object": "model"}]})
        model = (cap.json or {}).get("model", "")
        rid, seq = f"resp_{model}_{next(counter)}", itertools.count()
        out = [_sse("response.created", sequence_number=next(seq), response=_resp(rid, model, "in_progress"))]
        if model in ("primary-tool", "primary-tool-failed", "primary-tool-drop", "primary-nofault"):
            events, done = _function_call(seq, "call_primary", 0)
            out += events
            if model == "primary-tool":
                out.append(_sse("error", sequence_number=next(seq), error=SERVER_ERROR))
            elif model == "primary-tool-failed":
                out.append(_sse("response.failed", sequence_number=next(seq), response=_resp(
                    rid, model, "failed", error={"code": "server_error", "message": "upstream error"})))
            elif model == "primary-nofault":
                out.append(_sse("response.completed", sequence_number=next(seq), response=_resp(rid, model, "completed", [done])))
        elif model == "primary-prefail":
            out.append(_sse("error", sequence_number=next(seq), error=SERVER_ERROR))
        elif model == "fallback-tool":
            events, done = _function_call(seq, "call_fallback", 0)
            out += events + [_sse("response.completed", sequence_number=next(seq), response=_resp(rid, model, "completed", [done]))]
        elif model == "fallback-reasoning-tool":
            reasoning = {"id": "rs_fallback", "type": "reasoning", "summary": []}
            out += [_sse("response.output_item.added", sequence_number=next(seq), output_index=0, item=reasoning),
                    _sse("response.output_item.done", sequence_number=next(seq), output_index=0, item=reasoning)]
            events, done = _function_call(seq, "call_fallback", 1)
            out += events + [_sse("response.completed", sequence_number=next(seq), response=_resp(rid, model, "completed", [reasoning, done]))]
        else:
            return Reply.json({"error": {"message": f"unknown scenario model {model!r}"}}, 400)
        body = b"".join(out)
        if model == "primary-tool-drop":
            return Reply(200, {"Content-Type": "text/event-stream"}, body, declared_length=len(body) + 4096)
        return Reply(200, {"Content-Type": "text/event-stream"}, body)

    return respond


def proxy_config(upstream_url):
    base = f"{upstream_url}/v1"
    models = []
    for public, (primary, _) in SCENARIOS.items():
        models.append({"model_name": public, "litellm_params": {"model": f"openai/{primary}", "api_base": base, "api_key": "sk-kairo-upstream"}})
    for fallback in ("fallback-tool", "fallback-reasoning-tool"):
        models.append({"model_name": fallback, "litellm_params": {"model": f"openai/{fallback}", "api_base": base, "api_key": "sk-kairo-upstream"}})
    return {
        "model_list": models,
        "router_settings": {"fallbacks": [{public: [fb]} for public, (_, fb) in SCENARIOS.items()], "num_retries": 0},
        "litellm_settings": {"telemetry": False},
    }


def request_body(model, tool_choice="required"):
    return {"model": model, "stream": True, "input": "Append the line 'run' to the ledger.", "tools": TOOLS,
            "tool_choice": tool_choice}
