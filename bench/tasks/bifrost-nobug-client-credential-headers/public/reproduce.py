from kairo_verify import Upstream, show
from rigs import bifrost

from upstream import respond

with Upstream(respond) as up, bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
    status, body, raw = gw.post_json("/anthropic/v1/messages",
                                     {"model": "openai/gpt-4o", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
                                     {"x-api-key": "client-key", "api-key": "client-azure-key",
                                      "OpenAI-Organization": "org-client", "OpenAI-Project": "proj-client",
                                      "Authorization": "Bearer client-token"})
    show(f"client received HTTP {status}", raw)
    cap = up.last("/responses")
    show("headers Bifrost sent upstream", cap.headers if cap else "(nothing)")
