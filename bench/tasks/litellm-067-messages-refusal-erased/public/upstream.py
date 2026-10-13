"""Deterministic OpenAI Responses upstream: refusal, function call, or text, streaming or not."""

import json

from kairo_verify import Reply


def _resp(status, output):
    return {"id": "resp_kairo", "object": "response", "created_at": 0, "model": "captured-model", "status": status,
            "output": output, "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}}


def _ev(kind, **fields):
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **fields}, separators=(',', ':'))}\n\n"


def make_responder(refusal="REFUSALPROBE cannot help", text="PLAINPROBE hello"):
    def respond(cap):
        req = cap.json or {}
        prompt = json.dumps(req.get("input"))
        if "REFUSE" in prompt:
            part, kind = {"type": "refusal", "refusal": refusal}, "refusal"
            item = {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant", "content": [part]}
        elif "TOOL" in prompt:
            item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_weather",
                    "arguments": '{"city":"Oslo"}', "status": "completed"}
            part, kind = None, "tool"
        else:
            part, kind = {"type": "output_text", "text": text, "annotations": []}, "text"
            item = {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant", "content": [part]}
        if not req.get("stream"):
            return Reply.json(_resp("completed", [item]))
        seq = iter(range(100))
        frames = [_ev("response.created", sequence_number=next(seq), response=_resp("in_progress", []))]
        if kind == "tool":
            frames += [
                _ev("response.output_item.added", sequence_number=next(seq), output_index=0, item={**item, "arguments": "", "status": "in_progress"}),
                _ev("response.function_call_arguments.delta", sequence_number=next(seq), output_index=0, item_id="fc_1", delta=item["arguments"]),
                _ev("response.function_call_arguments.done", sequence_number=next(seq), output_index=0, item_id="fc_1", arguments=item["arguments"]),
                _ev("response.output_item.done", sequence_number=next(seq), output_index=0, item=item),
            ]
        else:
            empty = {"type": "refusal", "refusal": ""} if kind == "refusal" else {"type": "output_text", "text": "", "annotations": []}
            delta_kind = "response.refusal.delta" if kind == "refusal" else "response.output_text.delta"
            done_kind = "response.refusal.done" if kind == "refusal" else "response.output_text.done"
            value = part["refusal"] if kind == "refusal" else part["text"]
            done_field = {"refusal": value} if kind == "refusal" else {"text": value}
            frames += [
                _ev("response.output_item.added", sequence_number=next(seq), output_index=0, item={**item, "status": "in_progress", "content": []}),
                _ev("response.content_part.added", sequence_number=next(seq), output_index=0, content_index=0, item_id="msg_1", part=empty),
                _ev(delta_kind, sequence_number=next(seq), output_index=0, content_index=0, item_id="msg_1", delta=value),
                _ev(done_kind, sequence_number=next(seq), output_index=0, content_index=0, item_id="msg_1", **done_field),
                _ev("response.content_part.done", sequence_number=next(seq), output_index=0, content_index=0, item_id="msg_1", part=part),
                _ev("response.output_item.done", sequence_number=next(seq), output_index=0, item=item),
            ]
        frames.append(_ev("response.completed", sequence_number=next(seq), response=_resp("completed", [item])))
        return Reply(200, {"Content-Type": "text/event-stream"}, "".join(frames).encode())
    return respond


def proxy_config(upstream_url):
    return {
        "model_list": [{"model_name": "mock", "litellm_params": {
            "model": "openai/captured-model", "api_base": f"{upstream_url}/v1", "api_key": "sk-kairo-upstream"}}],
        "litellm_settings": {"telemetry": False},
    }


def messages_body(prompt, stream=False, tools=False):
    body = {"model": "mock", "max_tokens": 64, "messages": [{"role": "user", "content": prompt}]}
    if stream:
        body["stream"] = True
    if tools:
        body["tools"] = [{"name": "get_weather", "description": "Weather for a city",
                          "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}]
    return body
