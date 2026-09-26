from kairo_verify import Upstream, show
from rigs import switchyard

from upstream import make_responder

with Upstream(make_responder()) as up:
    cfg = switchyard.passthrough(f"{up.url}/v1", "openai_chat", client={"api_key_env": "KAIRO_BACKEND_KEY"})
    with switchyard.Gateway(cfg, env={"KAIRO_BACKEND_KEY": "sk-kairo-backend"}) as gw:
        status, body, raw = gw.post_json("/v1/chat/completions", {
            "model": "captured-model", "api_key": "sk-attacker", "organization": "org-attacker",
            "extra_headers": {"Authorization": "Bearer sk-attacker"}, "messages": [{"role": "user", "content": "hi"}]})
        show(f"client received HTTP {status}", raw)
        cap = up.last()
        show("upstream request headers", cap.headers)
        show("upstream request body", cap.json)
