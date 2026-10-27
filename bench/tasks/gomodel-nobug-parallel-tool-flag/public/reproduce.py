from kairo_verify import Upstream, show
from rigs import gomodel

from upstream import messages_body, respond

tool = {"name": "get_weather", "description": "Weather", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}
with Upstream(respond) as up, gomodel.Gateway(up.url + "/v1") as gw:
    status, body, raw = gw.post_json("/v1/messages", messages_body("weather in Paris and Rome?", tools=[tool],
                                                                   tool_choice={"type": "auto", "disable_parallel_tool_use": True}))
    show(f"client received HTTP {status}", raw)
    show("forwarded body", up.last("/chat/completions").json)
