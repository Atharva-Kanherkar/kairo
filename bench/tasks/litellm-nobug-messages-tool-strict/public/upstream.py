"""Deterministic OpenAI upstream: answers /v1/responses and /v1/chat/completions with a short text turn."""

from kairo_verify import Reply

RESPONSE = {"id": "resp_kairo", "object": "response", "created_at": 0, "model": "captured-model", "status": "completed",
            "output": [{"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok", "annotations": []}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
CHAT = {"id": "chatcmpl-kairo", "object": "chat.completion", "created": 0, "model": "captured-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def respond(cap):
    if cap.method == "GET":
        return Reply.json({"object": "list", "data": [{"id": "captured-model", "object": "model"}]})
    return Reply.json(RESPONSE if cap.path.rstrip("/").endswith("/responses") else CHAT)


def proxy_config(upstream_url):
    return {"model_list": [{"model_name": "mock", "litellm_params": {
        "model": "openai/captured-model", "api_base": f"{upstream_url}/v1", "api_key": "sk-kairo-upstream"}}],
        "litellm_settings": {"telemetry": False}}
