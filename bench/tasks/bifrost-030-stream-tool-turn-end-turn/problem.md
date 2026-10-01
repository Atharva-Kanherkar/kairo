# Claude Code stops before running a tool when streaming through `/anthropic/v1/messages`

We run Claude Code against Bifrost's Anthropic-compatible endpoint with an
OpenAI model behind it. When the model says a sentence and then calls a tool
in the same turn ("Let me check the time." followed by a `get_time` call), the
streamed response contains a complete `tool_use` block, but the final
`message_delta` says:

```json
{"type": "message_delta", "delta": {"stop_reason": "end_turn"}}
```

Claude Code treats the turn as finished and never runs the tool, so the
session stalls. Turns that are only a tool call stream the same wrong stop
reason. The same requests without streaming correctly return
`"stop_reason": "tool_use"`. This looks like a regression of #3638, which was
fixed once before.
