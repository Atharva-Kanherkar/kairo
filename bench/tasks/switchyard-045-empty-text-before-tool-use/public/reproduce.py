from kairo_verify import Upstream, show
from rigs import switchyard

from upstream import make_responder, messages_body

tools = [{"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
    status, body, raw = gw.post_json("/v1/messages", messages_body("TOOL please", tools=tools))
    show(f"client received HTTP {status}", raw)
