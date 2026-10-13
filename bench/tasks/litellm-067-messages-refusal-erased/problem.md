# Anthropic `/v1/messages` against an OpenAI model returns a blank turn when the model refuses

Our app talks to the LiteLLM proxy with the Anthropic SDK (`/v1/messages`),
and the model behind it is an OpenAI model (`openai/...` in `model_list`).
When the OpenAI model declines a request, the Responses API returns a
structured refusal:

```json
{"output": [{"type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "refusal", "refusal": "I can't help with that."}]}]}
```

LiteLLM hands our client this:

```json
{"type": "message", "role": "assistant", "content": [], "stop_reason": "end_turn"}
```

The refusal sentence is gone and nothing says the model refused, so the UI
shows an empty assistant message. Appending that empty turn to the history
makes the next request fail with `400 messages: text content blocks must be
non-empty`. With `"stream": true` the client receives no text at all either.

Requests sent directly to OpenAI, and the same prompt through LiteLLM's own
`/v1/chat/completions`, keep the refusal. We expected the Anthropic response to
carry the refusal text and to say that the model refused.
