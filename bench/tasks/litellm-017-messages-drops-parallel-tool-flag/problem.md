# Claude Code through LiteLLM runs tools in parallel even with `disable_parallel_tool_use: true`

Our agent talks to the LiteLLM proxy with the Anthropic Messages API and needs
tools to run one at a time, so every request sets:

```json
"tool_choice": {"type": "auto", "disable_parallel_tool_use": true}
```

The deployment is an OpenAI model (`openai/...`). The model keeps returning
several tool calls in one turn. Looking at what LiteLLM sends to OpenAI, the
request has `"tool_choice": "auto"` and nothing else: the "one tool at a time"
setting never reaches the provider. The call succeeds with HTTP 200 and no
warning.

When the same client uses LiteLLM's `/v1/chat/completions` with
`parallel_tool_calls: false`, the flag does reach OpenAI. We expected the
Anthropic setting to be translated to OpenAI's equivalent the same way.
