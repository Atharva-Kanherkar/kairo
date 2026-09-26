from kairo_verify import Upstream, show
from rigs import litellm

from upstream import proxy_config, respond

with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
    for prompt in ("EMPTY please", "hello"):
        status, body, raw = proxy.post_json("/v1/messages", {"model": "gem", "max_tokens": 32,
                                                             "messages": [{"role": "user", "content": prompt}]})
        show(f"{prompt!r}: client received HTTP {status}", raw)
