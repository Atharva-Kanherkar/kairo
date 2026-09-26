from kairo_verify import Upstream, show
from rigs import bifrost

from upstream import make_responder, messages_body

with Upstream(make_responder()) as up, bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
    for prompt in ("TRUNCATE list the steps", "FILTER tell me"):
        status, body, raw = gw.post_json("/anthropic/v1/messages", messages_body(prompt, tools=False))
        show(f"{prompt} non-stream: HTTP {status}", raw)
        status, _, raw = gw.post("/anthropic/v1/messages", messages_body(prompt, stream=True, tools=False))
        show(f"{prompt} stream: HTTP {status}", raw.decode())
