# `disable_parallel_tool_use` is ignored through Switchyard's `/v1/messages`

Our Anthropic-SDK agent must run tools sequentially and sends
`"tool_choice": {"type": "auto", "disable_parallel_tool_use": true}` (and, when
it forces a specific tool, `{"type": "tool", "name": "...",
"disable_parallel_tool_use": true}`). Switchyard fronts an OpenAI-compatible
backend for us, and the backend keeps returning several tool calls at once.
The forwarded chat completion request has `tool_choice` but no
`parallel_tool_calls`. Sending OpenAI's `parallel_tool_calls: false` to
Switchyard's `/v1/chat/completions` works, so the backend honors it; only the
Anthropic spelling of the setting is lost.
