from kairo_verify import Upstream, show
from rigs import litellm

from upstream import proxy_config, respond

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
    status, body, raw = proxy.post_json("/v1/messages", {"model": "mock", "max_tokens": 64,
        "tools": [{"name": "screenshot", "description": "Capture the screen", "input_schema": {"type": "object", "properties": {}}}],
        "messages": [
            {"role": "user", "content": "what is on screen?"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "screenshot", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": [
                {"type": "text", "text": "screen captured"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}]}]}]})
    show(f"client received HTTP {status}", raw)
    cap = up.last()
    show(f"forwarded to upstream {cap.path}", cap.json)
