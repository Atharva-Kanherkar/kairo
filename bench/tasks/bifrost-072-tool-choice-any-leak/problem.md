# `tool_choice: {"type": "any"}` through `/anthropic/v1/messages` gets a 400 from OpenAI

We point the Anthropic SDK at Bifrost's `/anthropic/v1/messages` endpoint and
route to OpenAI models. When we force tool use with Anthropic's
`tool_choice: {"type": "any"}`, OpenAI rejects every request:

```text
400 Invalid value: 'any'. Supported values are: 'none', 'auto', and 'required'.
```

The request Bifrost sends to OpenAI contains `"tool_choice": "any"`. This
happens both for the built-in `openai` provider (Responses API) and for a
custom OpenAI-compatible provider that only allows chat completions.
`{"type": "auto"}` and `{"type": "tool", "name": "..."}` work. Anthropic's
`any` means "the model must call some tool", which OpenAI calls `required`.

```python
import anthropic
client = anthropic.Anthropic(base_url="http://localhost:8080/anthropic", api_key="unused")
client.messages.create(
    model="openai/gpt-4o", max_tokens=256,
    tools=[{"name": "get_weather", "description": "Weather", "input_schema": {
        "type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}],
    tool_choice={"type": "any"},
    messages=[{"role": "user", "content": "Weather in Tokyo?"}],
)
```
