from kairo_verify import Upstream, show
from rigs import switchyard

from upstream import make_responder, messages_body

tool = {"name": "book_table", "description": "Book a table", "strict": True,
        "input_schema": {"type": "object", "properties": {"party": {"type": "integer"}}, "required": ["party"],
                         "additionalProperties": False}}
with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
    status, body, raw = gw.post_json("/v1/messages", messages_body("book for two", tools=[tool]))
    show(f"client received HTTP {status}", raw)
    show("forwarded tools", up.last().json.get("tools"))
