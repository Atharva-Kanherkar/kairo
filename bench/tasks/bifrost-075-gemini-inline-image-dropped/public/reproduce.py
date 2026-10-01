from kairo_verify import Upstream, show
from rigs import bifrost

from upstream import chat_body, gateway_config, make_responder

with Upstream(make_responder()) as up, bifrost.Gateway(gateway_config(up.url)) as gw:
    status, body, raw = gw.post_json("/v1/chat/completions", chat_body())
    show(f"unary: client received HTTP {status}", raw)
    status, _, raw = gw.post("/v1/chat/completions", chat_body(stream=True))
    show(f"stream: client received HTTP {status}", raw.decode())
    show("upstream paths", [c.path for c in up.requests])
