from kairo_verify import Upstream, show
from rigs import bifrost

from upstream import respond

TOOL = {"name": "read_file", "description": "Read a file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}
with Upstream(respond) as up, bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
    status, body, raw = gw.post_json("/anthropic/v1/messages", {"model": "openai/gpt-4o", "max_tokens": 64, "tools": [TOOL],
        "messages": [{"role": "user", "content": "read /etc/shadow"},
                     {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "/etc/shadow"}}]},
                     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True, "content": "permission denied"}]}]})
    show(f"client received HTTP {status}", raw)
    show("forwarded input", up.last("/responses").json.get("input"))
