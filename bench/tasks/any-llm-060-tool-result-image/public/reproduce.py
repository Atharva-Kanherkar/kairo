from kairo_verify import show
from rigs import anyllm

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
out = anyllm.call(
    tools=[{"name": "screenshot", "description": "Capture the screen", "input_schema": {"type": "object", "properties": {}}}],
    messages=[
        {"role": "user", "content": "what is on screen?"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "screenshot", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": [
            {"type": "text", "text": "here it is:"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}},
        ]}]},
    ],
)
show("client result", out.response.model_dump() if out.response is not None else repr(out.error))
show("forwarded to upstream", out.forwarded)
