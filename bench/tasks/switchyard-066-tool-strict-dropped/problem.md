# `strict: true` on Anthropic tool definitions never reaches the OpenAI backend

Our agent marks its tools `"strict": true` so the backend enforces the input
schema. Through Switchyard's `/v1/messages` (OpenAI-compatible backend), we get
tool arguments that violate the schema: missing required keys, extra keys.
The forwarded request shows the tool's name, description, and `parameters`
schema, but no `strict` flag on `function`. The same tool sent to Switchyard's
`/v1/chat/completions` with `function.strict: true` keeps the flag. We expected
Anthropic's `strict` to map onto OpenAI's `function.strict`.
