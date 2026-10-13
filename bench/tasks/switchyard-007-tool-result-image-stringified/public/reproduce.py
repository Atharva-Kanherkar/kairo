from kairo_verify import Upstream, show
from rigs import switchyard

from upstream import make_responder

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
    status, body, raw = gw.post_json("/v1/messages", {"model": "captured-model", "max_tokens": 64,
        "tools": [{"name": "screenshot", "description": "Capture", "input_schema": {"type": "object", "properties": {}}}],
        "messages": [{"role": "user", "content": "what is on screen?"},
                     {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "screenshot", "input": {}}]},
                     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": [
                         {"type": "text", "text": "screen captured"},
                         {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}]}]}]})
    show(f"client received HTTP {status}", raw)
    show("forwarded messages", up.last().json.get("messages"))
