from kairo_verify import Upstream, show
from rigs import litellm

from upstream import proxy_config, respond

with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
    status, body, raw = proxy.post_json("/v1/messages", {
        "model": "mock", "max_tokens": 256, "messages": [{"role": "user", "content": "book a table for two"}],
        "tools": [{"name": "book_table", "description": "Book a restaurant table", "strict": True,
                   "input_schema": {"type": "object", "properties": {"party": {"type": "integer"}},
                                    "required": ["party"], "additionalProperties": False}}]})
    show(f"client received HTTP {status}", raw)
    cap = up.last()
    show(f"forwarded to upstream {cap.path}", cap.json)
