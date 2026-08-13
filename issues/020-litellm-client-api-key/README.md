# 020, LiteLLM honors client `api_key` without admin opt-in, then routes later callers onto it

- **Upstream**: LiteLLM's own proxy ban list documents this class of bug as
  [Huntr 4001e1a2](https://huntr.com/bounties/4001e1a2-7b7a-4776-a3ae-e6692ec3d997)
  (`api_base` stealing the deployment key). `api_base` is now banned unless
  `allow_client_side_credentials` is on. `api_key` is **not** on that ban
  list. Adjacent: GHSA-jh89-88fc-qrfp / GHSA-3frq-6r6h-7j64 (the old
  "any api_key bypasses the ban list" hole, since removed). This is the
  remaining hole: `api_key` itself is still honored.
- **Tool under test**: LiteLLM 1.96.2. Mock OpenAI backend
  (`tools/litellm-mock.yaml`) plus live `gemini/gemini-2.5-flash`.
- **Not a credential incident**: every probe used fake canary tokens
  (`CANARY_*`). Error bodies and `/v1/models` were scanned for the real
  Gemini/OpenAI/Anthropic/OpenRouter keys. No leak. No rotation needed.
- **Reproduced**: 2026-08-13. Override 5/5 on mock and 5/5 on live Gemini.
  Cross-request stick 4/5 on mock (the original deployment still wins some
  router draws). Evidence: `transcripts/020/`.

## What breaks

A proxy caller who is not opted into BYOK can put `"api_key": "..."` in the
JSON body. LiteLLM forwards that value as `Authorization: Bearer ...` to the
configured upstream, replacing the deployment key in `config.yaml`.

On the OpenAI-compatible path the router then `upsert_deployment`s a new
backend with that key (`router.py` `_handle_clientside_credential`, gated
only by `is_clientside_credential` which is true whenever `api_key` is in
kwargs). Later requests that do **not** send `api_key` can be load-balanced
onto the injected deployment.

Who that hurts:

- Any tenant sharing the proxy. After one caller plants a key, some fraction
  of everyone else's prompts go out with it.
- Spend tracking and provider billing: usage lands on the planted key, not
  the admin's.
- The proxy as an unauthenticated relay: the client does not need
  `allow_client_side_credentials` or `configurable_clientside_auth_params`.

`api_base` / `base_url` in the same body are rejected (the Huntr fix). The
rejection currently surfaces as HTTP 500 `Internal server error` in this
install because the auth exception handler then `import prisma`s and
crashes. Fail-closed, so not exfil. Recorded, not frozen as the finding.

## Wire evidence

Three legs, same input shape.

1. **LiteLLM (mock OpenAI at 127.0.0.1:9996, deployment key `sk-x`)**
   - Body `api_key=CANARY_STICKY_API_KEY_r1` → upstream
     `Authorization: Bearer CANARY_STICKY_API_KEY_r1`
     (`transcripts/020/cap-override.jsonl`). 5/5.
   - Next body, no `api_key` → still
     `Authorization: Bearer CANARY_STICKY_API_KEY_r1`
     (`transcripts/020/cap-sticky.jsonl`). 4/5; run 3 drew the original
     `sk-x` deployment.
2. **Control: same proxy, no `api_key` in the body**
   - Upstream `Authorization: Bearer sk-x`
     (`transcripts/020/cap-control.jsonl`).
   - Direct Gemini with the real key: HTTP 200. Direct Gemini with
     `CANARY_INVALID_GEMINI_KEY`: HTTP 400 `API_KEY_INVALID`.
3. **Determinism**
   - Live LiteLLM → Gemini 2.5 Flash, body `api_key=CANARY_INVALID_GEMINI_KEY`:
     HTTP 401 wrapping Gemini's `API_KEY_INVALID`. 5/5. Same request without
     `api_key`: HTTP 200. 5/5.
   - Client header `x-goog-api-key: CANARY_INVALID_GEMINI_KEY` is **not**
     forwarded (HTTP 200, real key used). 5/5. Header forwarding is gated on
     `forward_client_headers_to_llm_api`, default off. Not a bug on default
     config.
   - `mock_response` is stripped. `/v1/models` does not echo keys or
     `api_base`.

Diff, live, every run:

| call | direct Gemini | LiteLLM |
|------|---------------|---------|
| valid configured key | 200 | 200 |
| invalid `api_key` in JSON body | 400 (direct uses header, not body) | **401 AuthenticationError** |
| invalid `x-goog-api-key` header | 400 | 200 (header dropped) |
| bad tool schema | 400 | 400 (same Gemini message, wrapped) |

## Root cause (in LiteLLM source)

Proxy ban list (`proxy/auth/auth_utils.py` `_BANNED_REQUEST_BODY_PARAMS`)
includes `api_base` and `base_url`. It does not include `api_key`.

The router treats `api_key` as a clientside credential anyway
(`router_utils/clientside_credential_handler.py`
`clientside_credential_keys = ["api_key", "api_base", "base_url"]`) and
`Router._handle_clientside_credential` upserts a new deployment with the
caller's key. That is supposed to be behind
`general_settings.allow_client_side_credentials`. The proxy never requires
that flag for `api_key` alone.

On Gemini the invalid planted key 401s and is cooled down, so later callers
keep working on the admin key. On any backend that accepts the planted
token (OpenAI-compatible mock, or a *valid* second provider key), the
injected deployment stays healthy and takes traffic.

## Test invariants

1. If the client did not send a credential the admin opted into, the
   forwarded `Authorization` / `x-goog-api-key` MUST be the deployment's.
2. A later request that omits `api_key` MUST still use the deployment key,
   never a previous caller's.

## Repro

```
python3 tools/capture_headers.py 9996 transcripts/020/cap-override.jsonl transcripts/020/canned-openai.json
# LiteLLM --config tools/litellm-mock.yaml --port 4000
curl -s localhost:4000/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"mock","messages":[{"role":"user","content":"sticky_with run=1"}],"api_key":"CANARY_STICKY_API_KEY_r1"}'
# capture Authorization is Bearer CANARY_STICKY_API_KEY_r1, not sk-x
curl -s localhost:4000/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"mock","messages":[{"role":"user","content":"sticky_without run=1"}]}'
# capture Authorization is often still the canary
```
