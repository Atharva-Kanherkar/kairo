from kairo_verify import Upstream, show
from rigs import switchyard

from upstream import make_responder, messages_body

with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
    status, body, raw = gw.post_json("/v1/messages", messages_body("FILTER please"))
    show(f"buffered: HTTP {status}", raw)
    status, _, raw = gw.post("/v1/messages", messages_body("FILTER please", stream=True))
    show(f"stream: HTTP {status}", raw.decode())
