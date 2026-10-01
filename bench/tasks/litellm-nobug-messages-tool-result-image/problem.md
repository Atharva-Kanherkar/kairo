# Screenshots in `tool_result` blocks vanish on `/v1/messages` with OpenAI models

Our computer-use agent returns screenshots from its `screenshot` tool inside
the Anthropic `tool_result` content, next to a short caption. With an OpenAI
deployment behind the LiteLLM proxy the model keeps saying it cannot see the
screen. We believe LiteLLM deletes the image bytes from tool results when it
translates Anthropic requests for OpenAI (this was reported for LiteLLM in
the past). Example tool result:

```json
{"type": "tool_result", "tool_use_id": "toolu_1", "content": [
  {"type": "text", "text": "screen captured"},
  {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo..."}}
]}
```

Please make sure tool-result images reach the model.
