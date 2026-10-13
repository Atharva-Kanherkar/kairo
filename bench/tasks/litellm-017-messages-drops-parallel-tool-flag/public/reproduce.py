from kairo_verify import Upstream, show
from rigs import litellm

from upstream import proxy_config, respond

tools = [{"name": "get_weather", "description": "Weather for a city",
          "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}]
with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
    status, body, raw = proxy.post_json("/v1/messages", {
        "model": "mock", "max_tokens": 64, "messages": [{"role": "user", "content": "weather in Paris and Rome?"}],
        "tools": tools, "tool_choice": {"type": "auto", "disable_parallel_tool_use": True}})
    show(f"client received HTTP {status}", raw)
    cap = up.last()
    show(f"forwarded to upstream {cap.path}", cap.json)
