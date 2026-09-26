# Codex runs a tool twice when a streaming `/v1/responses` call fails over to a fallback model

We run Codex CLI against the LiteLLM proxy with `router_settings.fallbacks`
configured. Occasionally the primary provider streams a complete function call
and then fails mid-stream with a retriable error (an in-band `error` event with
`server_error`, or `response.failed`). LiteLLM then calls the fallback
deployment and streams its answer into the same HTTP response.

The client receives the primary's function call, then a second
`response.created` and the fallback's function call for the same request.
Codex executes both. Our tool appends to a ledger, so a single user turn writes
two entries. Some OpenAI SDK versions also crash on the reused `output_index`.

Relevant config:

```yaml
model_list:
  - model_name: agent
    litellm_params: {model: openai/primary-model, api_base: "http://primary/v1", api_key: "..."}
  - model_name: agent-fallback
    litellm_params: {model: openai/fallback-model, api_base: "http://fallback/v1", api_key: "..."}
router_settings:
  fallbacks: [{"agent": ["agent-fallback"]}]
  num_retries: 0
general_settings:
  master_key: sk-...
```

Falling back is fine when the primary fails before it has sent anything. Once
the client has already received a tool call, starting the turn over on another
model replays it. When the connection simply drops after the tool call, LiteLLM
already reports the failure to the client and does not fall back, which is what
we expected in the in-band error case too.
