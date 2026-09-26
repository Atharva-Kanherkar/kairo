# Replayed extended-thinking history is silently dropped by the Messages bridge

I run an agent loop against `any_llm.messages()` with extended thinking. When the
provider is a non-Anthropic, OpenAI-compatible backend (`provider="openai"` with
`api_base` pointed at my server), each turn replays the assistant's previous
content, including its `thinking` block and signature.

The request succeeds with HTTP 200, but the assistant's thinking never reaches
the backend. Only the visible text of that turn is forwarded, so the model loses
the reasoning it produced on the previous turn.

```python
from any_llm import messages

messages(
    model="my-model",
    provider="openai",
    api_base="http://127.0.0.1:8000/v1",
    api_key="sk-...",
    max_tokens=64,
    messages=[
        {"role": "user", "content": "2+2?"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "Adding two and two gives four.", "signature": "sig-abc"},
            {"type": "text", "text": "4"},
        ]},
        {"role": "user", "content": "now 3+3"},
    ],
)
```

What my backend receives for the assistant turn:

```json
{"role": "assistant", "content": "4"}
```

This is lossy in one direction only: when my backend returns reasoning, any-llm
hands it to me as a `thinking` block in the Messages response, but when I send
that same block back on the next turn it disappears. I expected the replayed
thinking to reach the backend in the reasoning field of the assistant message,
not to vanish and not to be mixed into the visible text.
