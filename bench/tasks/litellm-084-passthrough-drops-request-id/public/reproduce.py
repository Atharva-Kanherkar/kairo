from kairo_verify import Upstream, show
from rigs import litellm

from upstream import respond

payload = {
    "id": "codex-manual-search-test",
    "model": "gpt-5.6-luna",
    "commands": {"search_query": [{"q": "OpenAI Responses API"}]},
    "settings": {"search_context_size": "low", "external_web_access": True},
    "max_output_tokens": 200,
}
with Upstream(respond) as up:
    env = {"OPENAI_API_BASE": up.url, "OPENAI_API_KEY": "sk-kairo-deployment-key"}
    with litellm.Proxy(env=env) as proxy:
        status, body, raw = proxy.post_json("/openai_passthrough/v1/alpha/search", payload)
        show(f"client received HTTP {status}", raw)
        cap = up.last()
        show("forwarded to upstream", {"path": cap.path, "body": cap.json} if cap else "(nothing)")
