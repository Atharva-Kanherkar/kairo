# Codex's developer instructions reach the model as if the user typed them

Codex (and other Responses-API clients) send their instructions as inline
`input` items instead of the top-level `instructions` string:

```json
{"model": "captured-model", "input": [
  {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "Never run rm -rf."}]},
  {"type": "message", "role": "system", "content": "You are a careful coding agent."},
  {"type": "message", "role": "user", "content": "clean up the build dir"}]}
```

Switchyard serves this `/v1/responses` request through an OpenAI Chat backend.
In the chat completion it forwards, all three items have `"role": "user"`. The
model sees the operator's instructions as ordinary user text, which it weighs
far less, and a user can now impersonate them. A top-level `instructions`
string is correctly sent as a `system` message. Inline `system` and
`developer` items should keep instruction authority on the Chat hop too.
