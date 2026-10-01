from kairo_verify import Upstream, show
from rigs import bifrost

from upstream import make_responder, messages_body

with Upstream(make_responder()) as up, bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
    status, _, raw = gw.post("/anthropic/v1/messages", messages_body("TEXT_THEN_TOOL what time is it?", stream=True))
    show(f"stream: client received HTTP {status}", raw.decode())
    status, body, raw = gw.post_json("/anthropic/v1/messages", messages_body("TEXT_THEN_TOOL what time is it?"))
    show(f"non-stream: client received HTTP {status}", raw)
