"""Deterministic OpenAI Responses upstream for the MCP auto-execution rig.

Round 1 (no function_call_output in the input): stream one completed function
call to the first function tool. Round 2 (a function_call_output is present),
or a request with no tools: stream one message with ``final_text``.
"""

import json

from kairo_verify import Reply


def _response(response_id, status, output):
    return {"id": response_id, "object": "response", "created_at": 1788782400, "status": status,
            "model": "mockmodel", "output": output, "parallel_tool_calls": True, "tool_choice": "auto",
            "tools": [], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                   "input_tokens_details": {"cached_tokens": 0},
                                   "output_tokens_details": {"reasoning_tokens": 0}}}


def _ev(kind, data):
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def make_responder(final_text="FINAL_FROM_ROUND_2", control_text="CONTROL_TEXT", argument="PING"):
    def respond(cap):
        if cap.method == "GET":
            return Reply.json({"object": "list", "data": [{"id": "mockmodel", "object": "model"}]})
        req = cap.json or {}
        follow_up = "function_call_output" in json.dumps(req)
        tools = req.get("tools") or []
        if follow_up or not tools:
            rid, text = "resp_round_2", (final_text if follow_up else control_text)
            item = {"id": "msg_round_2", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}
            frames = [
                _ev("response.created", {"type": "response.created", "response": _response(rid, "in_progress", []), "sequence_number": 0}),
                _ev("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}, "sequence_number": 1}),
                _ev("response.content_part.added", {"type": "response.content_part.added", "item_id": item["id"], "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}, "sequence_number": 2}),
                _ev("response.output_text.delta", {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0, "content_index": 0, "delta": text, "sequence_number": 3}),
                _ev("response.output_text.done", {"type": "response.output_text.done", "item_id": item["id"], "output_index": 0, "content_index": 0, "text": text, "sequence_number": 4}),
                _ev("response.content_part.done", {"type": "response.content_part.done", "item_id": item["id"], "output_index": 0, "content_index": 0, "part": item["content"][0], "sequence_number": 5}),
                _ev("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item, "sequence_number": 6}),
                _ev("response.completed", {"type": "response.completed", "response": _response(rid, "completed", [item]), "sequence_number": 7}),
            ]
        else:
            rid = "resp_round_1"
            name = next((t.get("name") for t in tools if t.get("type") == "function"), "echo")
            item = {"id": "fc_round_1", "type": "function_call", "call_id": "call_round_1", "name": name,
                    "arguments": json.dumps({"value": argument}, separators=(",", ":")), "status": "completed"}
            frames = [
                _ev("response.created", {"type": "response.created", "response": _response(rid, "in_progress", []), "sequence_number": 0}),
                _ev("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {**item, "arguments": "", "status": "in_progress"}, "sequence_number": 1}),
                _ev("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "item_id": item["id"], "output_index": 0, "delta": item["arguments"], "sequence_number": 2}),
                _ev("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": 0, "arguments": item["arguments"], "sequence_number": 3}),
                _ev("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item, "sequence_number": 4}),
                _ev("response.completed", {"type": "response.completed", "response": _response(rid, "completed", [item]), "sequence_number": 5}),
            ]
        return Reply(200, {"Content-Type": "text/event-stream"}, "".join(frames).encode())
    return respond


def proxy_config(upstream_url, call_log, mcp_script="/work/kairo/mcp_server.py"):
    return {
        "model_list": [{"model_name": "mock", "litellm_params": {
            "model": "openai/mockmodel", "api_base": f"{upstream_url}/v1", "api_key": "sk-kairo-upstream"}}],
        "mcp_servers": {"demo": {"transport": "stdio", "command": "/work/.venv/bin/python",
                                 "args": [mcp_script, call_log], "description": "deterministic local echo tool"}},
        "litellm_settings": {"telemetry": False},
    }


def request_body(mode, prompt="Use the echo tool with PING, then answer."):
    body = {"model": "mock", "stream": True, "input": prompt if mode != "no-tool" else "Answer without tools."}
    if mode != "no-tool":
        body["tools"] = [{"type": "mcp", "server_url": "litellm_proxy/mcp/demo", "server_label": "demo",
                          "require_approval": "never" if mode == "auto" else "always"}]
        body["tool_choice"] = "required"
    return body
