# `stop_sequences` are ignored when the Messages API is bridged to an OpenAI-compatible backend

I call `any_llm.messages()` with `provider="openai"` and `api_base` pointed at
my OpenAI-compatible inference server. I pass `stop_sequences` so generation
ends at a delimiter, but the model keeps generating right past it, and I pay
for the extra tokens. Against Anthropic directly the same code stops at the
delimiter. It looks like the bridge drops `stop_sequences` on the way out.

```python
from any_llm import messages

messages(
    model="my-model",
    provider="openai",
    api_base="http://127.0.0.1:8000/v1",
    api_key="sk-...",
    max_tokens=256,
    messages=[{"role": "user", "content": "List three fruits, then write END."}],
    stop_sequences=["END"],
)
```

Please make any-llm pass the stop sequences through to the backend.
