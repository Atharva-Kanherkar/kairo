"""Deterministic OpenAI Chat Completions upstream that records what GoModel forwards."""

from kairo_verify import Reply

CHAT = {"id": "chatcmpl-kairo", "object": "chat.completion", "created": 0, "model": "captured-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "{\"city\": \"Paris\"}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}


def respond(cap):
    if cap.method == "GET":
        return Reply.json({"object": "list", "data": [{"id": "captured-model", "object": "model"}]})
    return Reply.json(CHAT)


def messages_body(content="Where is the Eiffel Tower?", **extra):
    return {"model": "captured-model", "max_tokens": 128, "messages": [{"role": "user", "content": content}], **extra}
