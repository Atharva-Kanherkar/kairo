# Screenshots returned by a tool never reach the model when the Messages API is bridged

My browser agent calls a `screenshot` tool and returns the image to the model
inside the `tool_result`, the way the Anthropic Messages API documents it. With
`any_llm.messages(provider="openai", api_base=...)` pointed at an
OpenAI-compatible vision model, the model answers as if it never saw anything.

```python
from any_llm import messages

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
messages(
    model="my-vision-model",
    provider="openai",
    api_base="http://127.0.0.1:8000/v1",
    api_key="sk-...",
    max_tokens=64,
    tools=[{"name": "screenshot", "description": "Capture the screen",
            "input_schema": {"type": "object", "properties": {}}}],
    messages=[
        {"role": "user", "content": "what is on screen?"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "screenshot", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": [
            {"type": "text", "text": "here it is:"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}},
        ]}]},
    ],
)
```

The forwarded request contains the tool message with the text `here it is:`
and no image anywhere. The call returns 200 and nothing reports that the image
was removed. Images sent directly in a user message do reach the backend, so
the backend does support vision.
