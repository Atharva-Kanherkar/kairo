# `disable_parallel_tool_use` is ignored when the Messages API is bridged to an OpenAI-compatible provider

My agent executes tools sequentially, so it sends Anthropic's
`tool_choice.disable_parallel_tool_use: true` on every request to
`any_llm.messages()`. With the native Anthropic provider this works. With
`provider="openai"` and my own `api_base`, the backend keeps returning several
tool calls in one turn, and my loop runs them concurrently.

```python
from any_llm import messages

messages(
    model="my-model",
    provider="openai",
    api_base="http://127.0.0.1:8000/v1",
    api_key="sk-...",
    max_tokens=64,
    messages=[{"role": "user", "content": "weather in Paris and Rome?"}],
    tools=[{"name": "get_weather", "description": "Weather for a city",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}],
    tool_choice={"type": "auto", "disable_parallel_tool_use": True},
)
```

The request my backend receives has `"tool_choice": "auto"` and nothing that
asks for sequential tool use. The call returns 200 and nothing warns me that
the setting was dropped. I expected the OpenAI-side equivalent of the setting
to reach the backend.
