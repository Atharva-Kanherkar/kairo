# When the model refuses, `/anthropic/v1/messages` returns an empty assistant message

With an OpenAI model behind Bifrost, a refused request comes back from the
Responses API as a message whose only content part is a structured refusal:

```json
{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "I can't help with that."}]}
```

Bifrost's Anthropic-compatible endpoint (non-streaming) returns:

```json
{"type": "message", "role": "assistant", "content": [], "stop_reason": "end_turn"}
```

The refusal sentence is gone and nothing indicates that the model refused, so
our UI shows a blank reply and the next request with that empty turn in the
history is rejected by strict clients. Bifrost's OpenAI-compatible routes keep
the refusal. We expected the Anthropic response to contain the refusal text
and to report that the model refused.
