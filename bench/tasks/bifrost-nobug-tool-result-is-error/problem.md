# `is_error: true` on tool results is lost on `/anthropic/v1/messages`

When one of our tools fails, the agent reports it the Anthropic way:

```json
{"type": "tool_result", "tool_use_id": "toolu_1", "is_error": true, "content": "permission denied"}
```

With an OpenAI model behind Bifrost's Anthropic-compatible endpoint, the model
keeps treating failed tool calls as if they succeeded, and retries far less
than it does against Anthropic directly. Other gateways are known to drop
`is_error` when translating to OpenAI, and we suspect Bifrost does the same.
Please make sure the error flag survives translation.
