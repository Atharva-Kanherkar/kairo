from kairo_verify import show
from rigs import anyllm

tool = {"name": "get_weather", "description": "Weather for a city",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}
out = anyllm.call(
    messages=[{"role": "user", "content": "weather in Paris and Rome?"}],
    tools=[tool],
    tool_choice={"type": "auto", "disable_parallel_tool_use": True},
)
show("client result", out.response.model_dump() if out.response is not None else repr(out.error))
show("forwarded to upstream", out.forwarded)
