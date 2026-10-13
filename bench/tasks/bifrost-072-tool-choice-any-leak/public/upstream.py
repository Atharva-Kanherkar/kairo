"""Deterministic OpenAI upstream that always answers with one get_weather tool call."""

from kairo_verify import Reply

RESPONSES = {"id": "resp_kairo", "object": "response", "model": "gpt-4o", "status": "completed",
             "output": [{"type": "function_call", "id": "fc_1", "call_id": "call_kairo_1", "name": "get_weather",
                         "arguments": '{"location":"Tokyo"}', "status": "completed"}],
             "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
CHAT = {"id": "chatcmpl-kairo", "object": "chat.completion", "model": "gpt-4o",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                     "tool_calls": [{"id": "call_kairo_1", "type": "function",
                                     "function": {"name": "get_weather", "arguments": '{"location":"Tokyo"}'}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def respond(cap):
    if cap.method == "GET":
        return Reply.json({"object": "list", "data": [{"id": "gpt-4o", "object": "model"}]})
    return Reply.json(RESPONSES if cap.path.rstrip("/").endswith("/responses") else CHAT)


TOOLS = [{"name": "get_weather", "description": "Weather for a location", "input_schema": {
    "type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}]


def messages_body(model, tool_choice):
    return {"model": model, "max_tokens": 256, "tools": TOOLS, "tool_choice": tool_choice,
            "messages": [{"role": "user", "content": "Weather in Tokyo?"}]}
