from kairo_verify import Upstream, show
from rigs import bifrost

from upstream import messages_body, respond

with Upstream(respond) as up:
    cfg = bifrost.config({"openai": bifrost.openai_provider(up.url), "chatonly": bifrost.chat_only_provider(up.url)})
    with bifrost.Gateway(cfg) as gw:
        for model in ("openai/gpt-4o", "chatonly/gpt-4o"):
            up.reset()
            status, body, raw = gw.post_json("/anthropic/v1/messages", messages_body(model, {"type": "any"}))
            show(f"{model}: client received HTTP {status}", raw)
            cap = up.last()
            show(f"{model}: forwarded to upstream {cap.path if cap else ''}", cap.json if cap else "(nothing)")
