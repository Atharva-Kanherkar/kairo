# Replaying a Switchyard tool-call turn gets rejected: "text content blocks must be non-empty"

With an OpenAI-compatible backend behind Switchyard's `/v1/messages`, a turn in
which the model only calls a tool comes back (non-streaming) as:

```json
{"content": [{"type": "text", "text": ""}, {"type": "tool_use", "id": "call_1", "name": "lookup", "input": {}}],
 "stop_reason": "tool_use"}
```

The backend sent `"content": null` with `tool_calls`; the empty text block is
invented. Our agent appends the assistant turn to the history as-is, and the
next request to Anthropic (or any strict validator) fails with
`messages: text content blocks must be non-empty`. Streaming responses do not
contain the empty block, and neither do responses from Anthropic itself.
