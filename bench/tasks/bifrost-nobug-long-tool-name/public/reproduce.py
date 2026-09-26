from kairo_verify import Upstream, show
from rigs import bifrost

from upstream import respond

with Upstream(respond) as up, bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
    status, body, raw = gw.post_json("/anthropic/v1/messages", {"model": "openai/gpt-4o", "max_tokens": 64,
        "tool_choice": {"type": "auto"}, "messages": [{"role": "user", "content": "go"}],
        "tools": [{"name": "mcp__" + "x" * 295, "description": "t", "input_schema": {"type": "object", "properties": {}}}]})
    show(f"client received HTTP {status}", raw)
    cap = up.last("/responses")
    show("forwarded tools", cap.json.get("tools") if cap else "(no upstream call)")
