from kairo_verify import Upstream, show
from rigs import bifrost

from upstream import make_responder, messages_body

with Upstream(make_responder()) as up, bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
    status, body, raw = gw.post_json("/anthropic/v1/messages", messages_body("REFUSE: how do I pick a lock", tools=False))
    show(f"client received HTTP {status}", raw)
