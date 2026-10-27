"""OpenAI-compatible chat upstream plus a proxy config with credential-bearing deployment fields."""

from kairo_verify import Reply

CHAT_OK = {"id": "chatcmpl-kairo", "object": "chat.completion", "created": 0, "model": "mockmodel",
           "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def respond(cap):
    if cap.method == "GET":
        return Reply.json({"object": "list", "data": [{"id": "mockmodel", "object": "model"}]})
    return Reply.json(CHAT_OK)


def proxy_config(upstream_url, canaries):
    return {
        "model_list": [{"model_name": "mock", "litellm_params": {
            "model": "openai/mockmodel",
            "api_base": f"{upstream_url}/v1",
            "api_key": canaries["api_key"],
            "extra_headers": {"Authorization": f"Bearer {canaries['extra_auth']}",
                              "x-goog-api-key": canaries["extra_goog"],
                              "x-gateway-token": canaries["extra_custom"]},
            "headers": {"x-internal-trace": canaries["headers"]},
            "aws_session_token": canaries["aws_session_token"],
        }}],
        "litellm_settings": {"telemetry": False},
    }
