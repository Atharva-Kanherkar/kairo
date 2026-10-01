from kairo_verify import Upstream, show
from rigs import gomodel

from upstream import messages_body, respond

schema = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"], "additionalProperties": False}
with Upstream(respond) as up, gomodel.Gateway(up.url + "/v1") as gw:
    status, body, raw = gw.post_json("/v1/messages", messages_body(output_config={"format": {"type": "json_schema", "schema": schema}}))
    show(f"client received HTTP {status}", raw)
    show("forwarded body", up.last("/chat/completions").json)
