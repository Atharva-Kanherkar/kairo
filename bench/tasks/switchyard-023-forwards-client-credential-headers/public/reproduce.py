from kairo_verify import Upstream, show
from rigs import switchyard

from upstream import make_responder

with Upstream(make_responder()) as up:
    cfg = switchyard.passthrough(f"{up.url}/v1", "openai_chat", client={"api_key_env": "KAIRO_BACKEND_KEY"})
    with switchyard.Gateway(cfg, env={"KAIRO_BACKEND_KEY": "sk-kairo-backend"}) as gw:
        status, body, raw = gw.post_json("/v1/chat/completions", {"model": "captured-model", "messages": [{"role": "user", "content": "hi"}]},
                                         {"api-key": "client-azure-key", "OpenAI-Organization": "org-client",
                                          "OpenAI-Project": "proj-client", "x-api-key": "client-x-api-key"})
        show(f"client received HTTP {status}", raw)
        show("headers Switchyard sent upstream", up.last().headers)
