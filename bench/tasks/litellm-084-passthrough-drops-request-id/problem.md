# `/openai_passthrough` removes the request `id`, so OpenAI standalone search fails with `missing_required_parameter`

We route Codex through the LiteLLM proxy. Codex's standalone web search calls
OpenAI's `POST /v1/alpha/search`, which requires a top-level `id` in the JSON
body. Through LiteLLM's OpenAI pass-through route it fails every time:

```bash
curl -s http://localhost:4000/openai_passthrough/v1/alpha/search \
  -H 'content-type: application/json' -d '{
    "id": "codex-manual-search-test",
    "model": "gpt-5.6-luna",
    "commands": {"search_query": [{"q": "OpenAI Responses API"}]},
    "settings": {"search_context_size": "low", "external_web_access": true},
    "max_output_tokens": 200
  }'
```

```json
{"error": {"message": "Missing required parameter: 'id'.", "type": "invalid_request_error",
           "param": "id", "code": "missing_required_parameter"}}
```

Sending the same JSON directly to OpenAI works. The proxy runs with
`OPENAI_API_BASE` and `OPENAI_API_KEY` set and no other configuration. The
pass-through route is documented for exactly this case, calling provider
endpoints that LiteLLM does not model itself, so the provider's request body
should arrive intact.
