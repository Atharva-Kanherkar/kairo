from kairo_verify import Upstream, show
from rigs import litellm

from upstream import make_responder, messages_body, proxy_config

with Upstream(make_responder()) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
    status, body, raw = proxy.post_json("/v1/messages", messages_body("REFUSE: explain how to pick a lock"))
    show(f"client received HTTP {status}", raw)
    show("upstream request", up.last("/responses").json)
    status, _, raw = proxy.post("/v1/messages", messages_body("REFUSE: explain how to pick a lock", stream=True))
    show(f"streaming: client received HTTP {status}", raw.decode())
