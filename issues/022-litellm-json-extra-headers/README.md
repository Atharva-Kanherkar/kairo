# 022, LiteLLM honors JSON `extra_headers` / `headers` without the header-forwarding opt-in

- **Upstream**: no single ticket. Adjacent to LiteLLM 020 (`api_key` in the
  JSON body) and to the documented gate
  [`forward_client_headers_to_llm_api`](https://docs.litellm.ai/docs/proxy/forward_client_headers)
  (off by default, "for security reasons"). The Huntr 4001e1a2 ban list
  (`proxy/auth/auth_utils.py` `_BANNED_REQUEST_BODY_PARAMS`) covers
  `api_base`, `azure_ad_token`, STS tokens, and nested `extra_body` copies
  of those. It does not cover `extra_headers` or `headers`.
- **Tool under test**: LiteLLM 1.96.2. Mock OpenAI backend
  (`tools/litellm-mock.yaml`) plus live `gemini/gemini-2.5-flash`.
- **Not a credential incident**: every probe used fake canary tokens
  (`CANARY_*`). Live error bodies were scanned for the real
  Gemini/OpenAI/Anthropic/OpenRouter keys. No leak. No rotation needed.
- **Reproduced**: 2026-08-14. Mock forward 5/5 for `extra_headers` and
  `headers`. Live Gemini override 5/5 once router cooldown is disabled.
  Evidence: `transcripts/022/`.

## What breaks

A proxy caller can put provider secrets in the JSON body:

```
"extra_headers": {"x-goog-api-key": "...", "Authorization": "Bearer ..."}
```

or the sibling field `"headers": { ... }`. LiteLLM copies those maps onto
the outbound HTTP request. Default config does **not** forward the same
values when they arrive as real HTTP headers.

Who that hurts:

- Any tenant whose client (or a plugin) stuffs provider keys into
  `extra_headers` / `headers`. Those values leave the proxy and land at
  whatever backend the admin configured.
- Cross-provider credential leak: a Google key in `x-goog-api-key` is
  forwarded to a non-Google OpenAI-compatible backend.
- Auth override, not just a leak:
  - OpenAI-compatible backends: `Authorization` in either JSON field
    replaces the deployment `Bearer`.
  - Google AI Studio: `x-goog-api-key` in either JSON field replaces the
    deployment key. Live Gemini then 401s `API_KEY_INVALID`.
- Shared availability: unlike 020 (which upserts a *new* deployment for
  `api_key`), `extra_headers` poisons the *existing* deployment. One
  invalid canary 401s Gemini, and later callers who sent nothing get
  HTTP 429 `No deployments available` until cooldown expires.

The HTTP-header path is the control. Client header
`x-goog-api-key: CANARY_INVALID_GEMINI_KEY` is dropped (live HTTP 200,
real key used). 5/5. The JSON body is the bypass of that gate.

`allow_client_side_credentials` is not required.

## Wire evidence

Three legs, same input shape.

1. **LiteLLM (mock OpenAI at 127.0.0.1:9996, deployment key `sk-x`)**
   - Body `extra_headers.x-goog-api-key=CANARY_X_GOOG_API_KEY` appears on
     the upstream request. `Authorization` stays `Bearer sk-x`.
     5/5. `transcripts/022/cap-extra-headers.jsonl`.
   - Body `extra_headers.Authorization=Bearer CANARY_AUTHORIZATION`
     replaces the deployment key. 5/5.
     `transcripts/022/cap-extra-headers-auth.jsonl`.
   - Body `headers` (not `extra_headers`) does the same: x-* leak 5/5
     (`cap-headers-field.jsonl`) and `Authorization` override 5/5
     (`cap-headers-field-auth.jsonl`).
2. **Control**
   - Same proxy, no extra JSON fields: no goog canary, `Bearer sk-x`
     (`transcripts/022/cap-control.jsonl`).
   - Direct mock, no extra fields: no canaries
     (`transcripts/022/cap-direct-plain.jsonl`).
   - Direct Gemini with the real `x-goog-api-key`: HTTP 200. Direct
     Gemini with `CANARY_INVALID_GEMINI_KEY`: HTTP 400 `API_KEY_INVALID`.
3. **Determinism**
   - Live LiteLLM → Gemini 2.5 Flash, JSON
     `extra_headers.x-goog-api-key=CANARY_INVALID_GEMINI_KEY`: HTTP 401
     wrapping Gemini's `API_KEY_INVALID`. 5/5.
     Same for JSON `headers.x-goog-api-key`. Same request without those
     fields: HTTP 200. 5/5. Client *HTTP* header `x-goog-api-key` with
     the same canary: HTTP 200. 5/5.
     `transcripts/022/live-nocooldown.json`.
   - On default router settings, the first live 401 cools the real
     `gemini-flash` deployment. The next plain request is HTTP 429
     `No deployments available`. Recorded in
     `transcripts/022/live-results.json`. Not frozen as a separate
     finding; it is the availability follow-on of the override.

Diff, live, every run (cooldown disabled so later calls are not 429):

| call | direct Gemini | LiteLLM |
|------|---------------|---------|
| valid configured key | 200 | 200 |
| invalid HTTP `x-goog-api-key` | 400 | 200 (header dropped) |
| invalid JSON `extra_headers.x-goog-api-key` | 400 (direct has no such field) | **401 AuthenticationError** |
| invalid JSON `headers.x-goog-api-key` | 400 (direct has no such field) | **401 AuthenticationError** |

Same-format extra observation, not frozen as 022: `/v1/messages` with
`extra_headers.x-goog-api-key` still put the canary on the rewritten
`/v1/responses` upstream call (`transcripts/022/mock.jsonl` capture 27).
Only one hit before the mock proxy 429d.

## Root cause (in LiteLLM source)

`completion()` takes `extra_headers` as a first-class argument
(`main.py`) and also reads `headers` from kwargs, then merges:

```
headers = kwargs.get("headers", None) or extra_headers
if extra_headers is not None:
    headers.update(extra_headers)
```

The proxy unpacks the client JSON into `completion(**data)`, so those
fields reach the provider client with no `allow_client_side_credentials`
check.

On Google AI Studio, `_get_token_and_url` sets
`auth_header = {"x-goog-api-key": gemini_api_key}`.
`VertexGeminiConfig.validate_environment` then does
`default_headers.update(headers)` *after* writing that key, so a client
`x-goog-api-key` wins.

On the OpenAI-compatible path, the SDK `extra_headers` map is passed
through as HTTP headers, so `Authorization` in that map replaces the
deployment bearer.

HTTP client headers are a different path, gated on
`forward_client_headers_to_llm_api` (default off). That is why the HTTP
control stays 200 and the JSON body does not.

## Test invariants

1. If the client did not send a header the admin opted to forward, that
   header's value MUST NOT appear on the upstream request. JSON
   `extra_headers` / `headers` are not an opt-in.
2. The forwarded `Authorization` MUST stay the deployment's unless the
   admin opted into client credentials.

## Repro

```
python3 tools/capture_headers.py 9996 transcripts/022/cap-extra-headers.jsonl transcripts/020/canned-openai.json
# LiteLLM --config tools/litellm-mock.yaml --port 4000
curl -s localhost:4000/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"mock","messages":[{"role":"user","content":"ping"}],"extra_headers":{"x-goog-api-key":"CANARY_X_GOOG_API_KEY"}}'
# capture headers include x-goog-api-key: CANARY_X_GOOG_API_KEY
```
