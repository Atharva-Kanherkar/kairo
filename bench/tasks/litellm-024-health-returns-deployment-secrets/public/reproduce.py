from kairo_verify import Upstream, show
from rigs import litellm

from upstream import proxy_config, respond

CANARIES = {"api_key": "sk-CANARY-API-KEY", "extra_auth": "CANARY-EXTRA-AUTH", "extra_goog": "CANARY-GOOG-KEY",
            "extra_custom": "CANARY-GATEWAY-TOKEN", "headers": "CANARY-HEADERS-FIELD",
            "aws_session_token": "CANARY-AWS-SESSION-TOKEN"}
with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url, CANARIES)) as proxy:
    status, body, raw = proxy.post_json("/chat/completions", {"model": "mock", "messages": [{"role": "user", "content": "hi"}]})
    show("chat: headers the upstream received", {k: v for k, v in up.last("/chat/completions").headers.items()
                                                  if k in ("authorization", "x-goog-api-key", "x-gateway-token", "x-internal-trace")})
    status, _, raw = proxy.get("/health")
    show(f"GET /health -> HTTP {status}", raw)
