import sys

from kairo_verify import Upstream, show
from rigs import litellm

from upstream import SCENARIOS, make_responder, proxy_config, request_body

scenario = sys.argv[1] if len(sys.argv) > 1 else "agent-error"
assert scenario in SCENARIOS, f"pick one of {', '.join(SCENARIOS)}"
with Upstream(make_responder()) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
    status, headers, raw = proxy.post("/v1/responses", request_body(scenario))
    show(f"{scenario}: client received HTTP {status} (raw SSE)", raw.decode())
    show("upstream requests (model per call)", [c.json.get("model") for c in up.requests if c.method == "POST"])
