# 026, LiteLLM honors JSON `extra_headers` / `headers` / `organization` without the header-forwarding opt-in

- **Upstream**: no single ticket. Adjacent to 020 (body `api_key` is not on
  the Huntr 4001e1a2 ban list) and to the documented gate
  [`forward_client_headers_to_llm_api`](https://docs.litellm.ai/docs/proxy/forward_client_headers)
  (off by default, "for security reasons"). `_BANNED_REQUEST_BODY_PARAMS`
  covers `api_base`, `azure_ad_token`, STS tokens, and nested `extra_body`
  copies of those. It does not cover `extra_headers`, `headers`, or
  `organization`.
- **Tool under test**: LiteLLM 1.96.2. Mock OpenAI backend
  (`transcripts/026/litellm-mock.yaml`) plus live `openai/gpt-4o-mini` and
  `gemini/gemini-2.5-flash`.
- **Not a credential incident**: probes used fake canaries. Live error
  bodies were scanned for the real Gemini/OpenAI/Anthropic/OpenRouter keys.
  No leak. No rotation needed.
- **Reproduced**: 2026-08-14. Mock header injection 5/5. Live OpenAI
  invalid `organization` 401 `mismatched_organization` 2/2 before the
  router cooled the deployment down. Live Gemini invalid
  `extra_headers.Authorization` 401 dual-auth 2/2, then the same cooldown.
  Evidence: `transcripts/026/`.

## What breaks

A caller who is not opted into header forwarding can put credentials and
tenant selectors in the JSON body. LiteLLM copies them onto the outbound
provider request. HTTP headers with the same names are still dropped on
the default config (020's control). The JSON fields are the bypass.

Who that hurts:

- OpenAI deployments: `"organization": "org-..."` becomes the
  `OpenAI-Organization` header while the admin Bearer stays. Live, an
  invalid org is HTTP 401 `OpenAI-Organization header should match
  organization for API key`, matching the direct-OpenAI control. A valid
  org id the admin key can access would bill that org instead.
  `extra_headers.OpenAI-Organization` is the same header on the mock
  wire 5/5.
- OpenAI-compatible `api_base`: `extra_headers.Authorization` or
  `headers.Authorization` **replaces** the deployment Bearer. Mock 5/5.
  That is 020's key swap through a second door, without the sticky
  router upsert (a later control request still used `sk-x` unless a
  body `api_key` had been planted).
- Azure-style backends: `extra_headers.api-key` is forwarded next to the
  deployment Bearer. Mock 5/5. Azure authenticates with `api-key`.
- Gemini: `extra_headers.Authorization` is sent **in addition to** the
  Google key. Live 401 `Expected only one form of authentication`. The
  401 puts the deployment on LiteLLM's cooldown list, so later callers
  who sent nothing extra get HTTP 429 `No deployments available`. One
  tenant can knock the model out for everyone else.
- `extra_headers.x-goog-api-key` is on the mock wire 5/5. Live Gemini
  probes for that field hit the same cooldown 429, so the live auth-swap
  question is unanswered. The mock is what proves the header left.

`forward_client_headers_to_llm_api` stays off. Body `api_base` /
`extra_body.api_base` stay rejected (Huntr fix, HTTP 500 on this
install). `default_headers` did not replace Authorization. A connection
error to a closed `api_base?key=` did **not** echo the query key (the
Switchyard 025 shape is not a LiteLLM client leak). Those are the
controls and the honest negatives.

```mermaid
sequenceDiagram
    participant Caller
    participant LL as LiteLLM
    participant OpenAI as Upstream

    Note over Caller,OpenAI: Documented-safe path: HTTP headers are not forwarded
    Caller->>LL: POST /v1/chat/completions<br/>header OpenAI-Organization: WRONG
    LL->>OpenAI: Authorization: Bearer OFFICE_KEY
    OpenAI-->>LL: 200
    LL-->>Caller: 200

    Note over Caller,OpenAI: Bug: JSON organization becomes the same header
    Caller->>LL: body organization: WRONG_ORG
    LL->>OpenAI: Authorization: Bearer OFFICE_KEY<br/>OpenAI-Organization: WRONG_ORG
    OpenAI-->>LL: 401 mismatched_organization
    LL-->>Caller: 401
    Note over LL: router then cools the deployment<br/>later callers get 429
```

## Wire evidence

Three legs.

1. **LiteLLM (mock OpenAI at 127.0.0.1:9996, deployment key `sk-x`)**
   - Body `extra_headers.Authorization=Bearer CANARY_BODY_EXTRA_HEADERS_AUTH`
     → upstream `Authorization: Bearer CANARY_BODY_EXTRA_HEADERS_AUTH`.
     5/5. `transcripts/026/cap-extra-headers-auth.jsonl`.
   - Body `headers.Authorization=Bearer CANARY_BODY_HEADERS_AUTH` → same
     swap. 5/5. `transcripts/026/cap-headers-auth.jsonl`.
   - Body `organization=CANARY_BODY_ORGANIZATION` → upstream
     `openai-organization: CANARY_BODY_ORGANIZATION`, Bearer stays `sk-x`.
     5/5. `transcripts/026/cap-organization.jsonl`.
   - Body `extra_headers.OpenAI-Organization=CANARY_BODY_OPENAI_ORG` →
     same org header. 5/5. `transcripts/026/cap-extra-headers-org.jsonl`.
   - Body `extra_headers.api-key=CANARY_BODY_AZURE_API_KEY` → upstream
     `api-key`. 5/5. `transcripts/026/cap-extra-headers-azure.jsonl`.
   - Body `extra_headers.x-goog-api-key=CANARY_BODY_X_GOOG` → upstream
     `x-goog-api-key`. 5/5. `transcripts/026/cap-extra-headers-goog.jsonl`.
2. **Control**
   - Same proxy, no extra JSON fields: upstream `Authorization: Bearer sk-x`,
     no canaries. `transcripts/026/cap-control.jsonl`.
   - Body `extra_body.api_base` is rejected HTTP 500. 5/5.
   - Closed-port chat HTTP 500 `Connection error`, no `CANARY_DOWN_QUERY_KEY`
     in the client body. 5/5. `transcripts/026/closed-error.json`.
   - Direct OpenAI with the real bearer: HTTP 200. Direct OpenAI with
     `OpenAI-Organization: org-CANARYINVALIDORG`: HTTP 401
     `mismatched_organization`. 5/5.
3. **Determinism / live**
   - Live LiteLLM → `gpt-4o-mini` with body `organization:
     org-CANARYINVALIDORG`: HTTP 401 wrapping the same OpenAI message.
     2/2, then the deployment entered cooldown (later probes 429).
   - Live LiteLLM → `gemini-2.5-flash` with
     `extra_headers.Authorization: Bearer CANARY_INVALID_BEARER`: HTTP 401
     wrapping Gemini `Expected only one form of authentication`. 2/2, then
     cooldown 429.
   - Live controls without those fields: OpenAI 200, Gemini 200, before
     cooldown. `transcripts/026/live-results.json`.

## Root cause (in LiteLLM source)

`proxy/auth/auth_utils.py` `_BANNED_REQUEST_BODY_PARAMS` never lists
`extra_headers`, `headers`, or `organization`. Those names are in
`OPENAI_PARAMS` (`constants.py`), so they flow through as OpenAI SDK
kwargs. The SDK turns `organization` into `OpenAI-Organization` and
merges `extra_headers` / `headers` onto the outbound request. HTTP
header forwarding is a separate gate (`forward_client_headers_to_llm_api`,
default off). The JSON path does not consult that gate.

## Test invariants

1. A client JSON `extra_headers.Authorization` or `headers.Authorization`
   MUST NOT replace the deployment Bearer.
2. A client JSON `organization` or `extra_headers.OpenAI-Organization`
   MUST NOT appear as an upstream `OpenAI-Organization` header.
3. A request with none of those fields MUST keep the deployment Bearer
   and MUST NOT grow the canary headers.

## Repro

```
python3 tools/capture_headers.py 9996 transcripts/026/cap-ll.jsonl
# LiteLLM --config transcripts/026/litellm-mock.yaml --port 4000
curl -s localhost:4000/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"mock","messages":[{"role":"user","content":"x"}],"max_tokens":8,"organization":"CANARY_BODY_ORGANIZATION"}'
# capture has header openai-organization: CANARY_BODY_ORGANIZATION
```
