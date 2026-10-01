from kairo_verify import Upstream, show
from rigs import switchyard

from upstream import make_responder, messages_body

tools = [{"name": "get_weather", "description": "Weather", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}]
with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
    status, body, raw = gw.post_json("/v1/messages", messages_body("weather in Paris and Rome?", tools=tools,
                                                                   tool_choice={"type": "auto", "disable_parallel_tool_use": True}))
    show(f"client received HTTP {status}", raw)
    fwd = up.last().json
    show("forwarded tool_choice / parallel_tool_calls", {"tool_choice": fwd.get("tool_choice"), "parallel_tool_calls": fwd.get("parallel_tool_calls")})
