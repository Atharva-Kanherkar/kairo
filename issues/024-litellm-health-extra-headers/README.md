# 024, LiteLLM GET /health returns deployment `extra_headers` and `aws_session_token` in full

- **Upstream**: [litellm#36898](https://github.com/BerriAI/litellm/issues/36898). `/health` already hides `api_base` /
  `api_version` from non-admins (`_strip_admin_only_fields_from_health_result`).
  `api_key` is on `ILLEGAL_DISPLAY_PARAMS` and is stripped. `extra_headers`,
  `headers`, and `aws_session_token` are on neither list, so they are copied
  into the JSON the caller sees. Adjacent: `/model/info` docs say they omit
  api key and api base; `api_key` is popped, `api_base` is not, and
  `SensitiveDataMasker` still shows first-4 plus last-4 of named secrets.
- **Tool under test**: LiteLLM 1.96.2. Mock OpenAI backend
  (`transcripts/024/litellm-leak.yaml`) plus live `gemini/gemini-2.5-flash`
  with the real `GEMINI_API_KEY` in `extra_headers.x-goog-api-key`.
- **Credential incident (local, this hunt)**: live `GET /health` returned
  the full Gemini key 5/5. Live `/model/info` returned first-4 plus last-4
  of the same key 5/5. Transcripts are redacted
  (`transcripts/024/live-health-redacted.json`). Rotate `GEMINI_API_KEY`.
- **Reproduced**: 2026-08-14. Canary echo 5/5 on mock. Live Gemini key echo
  5/5 on `/health`. Live chat to `gemini-2.5-flash` HTTP 200, no key in the
  completion. Evidence: `transcripts/024/`.

## What breaks

An admin puts provider auth in `litellm_params.extra_headers` (Azure
`api-key`, Google `x-goog-api-key`, a Bearer token) or an AWS
`aws_session_token` on the deployment. Any caller of `GET /health` gets
those values back in plaintext.

Default local proxy has no master key, so `user_api_key_auth` lets the
request through. Even when a key is required, the same endpoint only
strips `api_base` / `api_version` for non-admins. `extra_headers` stay.

Who that hurts:

- Shared proxies: a tenant who can call `/health` reads the admin's
  upstream credentials and can call the provider directly.
- AWS deployments: `aws_session_token` is a short-lived cloud credential.
  It is not on `ILLEGAL_DISPLAY_PARAMS`, so it is not even masked.
- The sibling `/model/info` path is the weaker copy of the same class.
  Docs claim it omits api key and api base. `api_key` is popped.
  `api_base` (including `?key=`) is returned in full. Named
  `extra_headers` are masked to first-4 plus last-4. Header names that do
  not match the masker (`x-custom-internal-header`) come back in full.

`/v1/models`, `/health/liveliness`, and a normal chat completion do not
echo these fields. Those are the controls.

```mermaid
flowchart LR
  adminYaml["config.yaml extra_headers and aws_session_token"] --> proxy["LiteLLM 1.96.2"]
  caller["any caller GET /health"] --> proxy
  proxy -->|"strips api_key and api_base"| drop["not in body"]
  proxy -->|"returns extra_headers and aws_session_token in full"| caller
```

## Wire evidence

Three legs, same mock deployment.

1. **LiteLLM (mock OpenAI at 127.0.0.1:9996, deployment `api_key=sk-x-CANARY_DEPLOYMENT_API_KEY`)**
   - `GET /health` returns `extra_headers.Authorization=Bearer CANARY_EXTRA_HEADERS_AUTHORIZATION`,
     `x-goog-api-key=CANARY_X_GOOG_API_KEY_VALUE`,
     `api-key=CANARY_AZURE_STYLE_API_KEY`, and
     `aws_session_token=CANARY_AWS_SESSION_TOKEN_VALUE` in full.
     `api_key` is absent. `api_base` is absent (the admin-only strip).
     5/5. `transcripts/024/health.json`.
   - `GET /model/info` returns `api_base=http://127.0.0.1:9996/v1?key=CANARY_QUERY_KEY_IN_BASE`
     in full, `x-custom-internal-header=CANARY_UNMASKED_HEADER_VALUE` in
     full, and named secrets as first-4 plus last-4. `api_key` is absent.
     5/5. `transcripts/024/model-info.json`. Adjacent, not the frozen hole.
2. **Control**
   - `GET /v1/models`: model name only. No canaries.
     `transcripts/024/models-control.json`.
   - `GET /health/liveliness`: `"I'm alive!"`. No canaries.
     `transcripts/024/liveliness-control.json`.
   - `POST /v1/chat/completions` to live `gemini-2.5-flash`: HTTP 200. No
     key in the completion. 5/5.
   - Live `GET /health` with the real Gemini key in `extra_headers`:
     HTTP 200, `healthy_count` 1 (real Gemini probe succeeded), body
     contains the full key. 5/5.
     `transcripts/024/live-health-redacted.json`.
   - Live `GET /model/info`: first-4 plus last-4 of the same Gemini key.
     5/5.
3. **Determinism**
   - Mock `/health` and `/model/info` 5/5. Compact log:
     `transcripts/024/client-results.json`.
   - Live key echo 5/5. `transcripts/025/live-real-scoreboard.json`
     (024 rows are `ll_health` / `ll_model_info`).

## Root cause (in LiteLLM source)

`proxy/health_check.py` `_clean_endpoint_data` merges the whole
`litellm_params` dict into the `/health` row, then drops
`ILLEGAL_DISPLAY_PARAMS` (`api_key`, `messages`, `vertex_credentials`,
`aws_access_key_id`, `aws_secret_access_key`, ...). It does not drop
`extra_headers`, `headers`, or `aws_session_token`, and it does not run
`SensitiveDataMasker`.

`proxy/health_endpoints/_health_endpoints.py`
`_strip_admin_only_fields_from_health_result` then drops only
`api_base` and `api_version` for non-admins. That is why this capture
has no `api_base` and still has every extra header.

`/model/info` uses a different sanitizer
(`remove_sensitive_info_from_deployment`): pop `api_key`, then mask
remaining keys whose names look like secrets. `api_base` is not popped
and is not a sensitive name.

## Test invariants

1. A client-visible body MUST NOT contain deployment `extra_headers`
   values or `aws_session_token`.
2. `/v1/models`, `/health/liveliness`, and a chat completion that did not
   send those secrets MUST stay clean.

## Repro

```
python3 tools/capture_headers.py 9996 transcripts/024/cap-upstream.jsonl
# LiteLLM --config transcripts/024/litellm-leak.yaml --port 4000
curl -s localhost:4000/health
# extra_headers.Authorization is Bearer CANARY_EXTRA_HEADERS_AUTHORIZATION
curl -s localhost:4000/v1/models
# no canaries
```
