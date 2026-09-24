# `thinking: {"type": "adaptive"}` is accepted and silently ignored on `/v1/messages` with an OpenAI-compatible model

I run OGX with an OpenAI-compatible inference provider (`OPENAI_BASE_URL`) and
point an Anthropic-SDK client at OGX's `/v1/messages`. Our client sends
`thinking: {"type": "adaptive"}`, which is the recommended thinking mode for
current Claude models.

```bash
curl -s http://localhost:8321/v1/messages -H 'content-type: application/json' -d '{
  "model": "openai/my-model",
  "max_tokens": 256,
  "thinking": {"type": "adaptive"},
  "messages": [{"role": "user", "content": "Plan a three-step migration."}]
}'
```

The response is HTTP 200 with an ordinary answer. The chat completion request
OGX sends to the provider contains no thinking or reasoning setting at all, so
the model runs without the mode the caller asked for, and nothing tells the
caller.

The same request with `thinking: {"type": "enabled", "budget_tokens": 1024}`
is rejected with a clear HTTP 400 explaining that translation mode does not
support extended thinking. Adaptive thinking should not quietly get a
different outcome: either honor it or refuse it the way enabled thinking is
refused.
