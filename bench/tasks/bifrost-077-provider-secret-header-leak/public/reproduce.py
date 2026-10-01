from kairo_verify import Upstream, show
from rigs import bifrost

from upstream import VIRTUAL_KEY, gateway_config, respond, responses_body

cfg = gateway_config(None, {"X-Provider-Secret": "CANARY_PROVIDER_SECRET", "X-Safe-Trace": "trace-1"})
with Upstream(respond) as up:
    cfg["providers"]["mockoai"]["network_config"]["base_url"] = up.url
    with bifrost.Gateway(cfg) as gw:
        status, headers, raw = gw.post("/v1/responses", responses_body(), {"x-bf-vk": VIRTUAL_KEY})
        show(f"virtual-key caller: HTTP {status}, response headers", headers)
        show("response body", raw)
