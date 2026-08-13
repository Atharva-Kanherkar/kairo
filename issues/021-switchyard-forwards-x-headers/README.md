# 021, Switchyard forwards client `x-*` secrets to the upstream by default

- **Upstream**: no single ticket. Adjacent to LiteLLM's gated
  `forward_client_headers_to_llm_api` (off by default). Switchyard's
  `RESERVED_HEADERS` in `libsy-llm-client/src/client.rs` strips
  `authorization`, `x-api-key`, and `cookie`, then forwards everything else.
  `x-goog-api-key` is not reserved.
- **Tool under test**: Switchyard `switchyard-server` 0.2.0 (commit 2bef154).
- **Not a credential incident**: probes used fake canary tokens. Live Gemini
  error bodies were scanned for real keys. No leak. No rotation needed.
- **Reproduced**: 2026-08-13. Header forward 5/5 on the capture rig (OpenAI
  chat ingress and Anthropic `/v1/messages` ingress). Live Gemini 5/5: the
  extra header does not override the configured bearer (Google prefers
  `Authorization`), but the mock shows it is still sent. Evidence:
  `transcripts/021/`.

## What breaks

A Switchyard caller can send `x-goog-api-key` or any other non-reserved
header (including `x-custom-internal-secret`). Switchyard copies the inbound
`HeaderMap` onto `Metadata.http_headers` and
`forward_metadata_headers` attaches those values to the upstream HTTP call.

Who that hurts:

- Any tenant whose client (or a malicious plugin) attaches provider keys or
  internal tokens as `x-*` headers. Those values leave the proxy and land
  at whatever backend the admin configured: OpenAI, OpenRouter, Anthropic,
  Gemini.
- Cross-provider credential leak: a Google key in `x-goog-api-key` is
  forwarded to a non-Google backend.
- The reserved list *does* protect the deployment key:
  client `Authorization` / `x-api-key` / `Cookie` are dropped, and
  `apply_auth` then sets `Authorization: Bearer <config>`.

LiteLLM only does this when `forward_client_headers_to_llm_api` is true.
Switchyard does it with no flag.

## Wire evidence

Three legs.

1. **Switchyard (mock OpenAI at 127.0.0.1:9999, deployment `Bearer x`)**
   - Client headers `x-goog-api-key=CANARY_X_GOOG_API_KEY` and
     `x-custom-internal-secret=CANARY_X_CUSTOM_SECRET` appear on the
     upstream request. `Authorization` stays `Bearer x`.
     `x-api-key` and `Cookie` are stripped. 5/5.
     `transcripts/021/cap-headers.jsonl` (chat), 
     `transcripts/021/cap-anthropic-headers.jsonl` (Anthropic ingress,
     still forwarded onto the OpenAI mock).
2. **Control**
   - Same Switchyard, no extra headers: no goog canary
     (`transcripts/021/cap-control.jsonl`).
   - Direct mock with no extra headers: no canaries.
   - Direct Gemini OpenAI-compat with a real bearer: HTTP 200. Direct
     Gemini with only `x-goog-api-key: CANARY_INVALID_GEMINI_KEY`: HTTP 400
     `Missing or invalid Authorization header.` Direct Gemini with real
     bearer *plus* the invalid `x-goog-api-key`: HTTP 200 (Google prefers
     bearer).
3. **Determinism**
   - Live Switchyard → Gemini 2.5 Flash with client `x-goog-api-key:
     CANARY_INVALID_GEMINI_KEY`: HTTP 200, 5/5 (configured bearer wins).
     The mock is what proves the header still went out.

Same-format extra finding, not frozen as 021: a JSON body `api_key` is
forwarded verbatim (`transcripts/021/cap-body-apikey.jsonl`, 5/5). Live
Gemini then 400s `Unknown name "api_key"`. Unlike LiteLLM 020, Switchyard
does **not** swap the `Authorization` header for that body field.

## Root cause (in Switchyard source)

`switchyard-server/src/lib.rs` `metadata_from_headers` stores the entire
inbound `HeaderMap`. `libsy-llm-client/src/client.rs` `send_once`:

```
forward_metadata_headers  // all non-reserved client headers
apply_extra_headers       // config
apply_auth                // deployment key last, so it wins for Authorization / x-api-key
```

`RESERVED_HEADERS` has no `x-goog-api-key` and no general `x-` deny.

## Test invariants

1. A client `x-goog-api-key` (or any secret-bearing `x-*` header not used
   for proxy auth) MUST NOT appear on the upstream request.
2. A request that did not send that header MUST NOT grow it.

## Repro

```
python3 tools/capture_headers.py 9999 transcripts/021/cap-headers.jsonl transcripts/020/canned-openai.json
tools/switchyard/target/release/switchyard-server --config tools/switchyard-capture.toml --port 9000
curl -s localhost:9000/v1/chat/completions \
  -H 'content-type: application/json' \
  -H 'x-goog-api-key: CANARY_X_GOOG_API_KEY' \
  -d '{"model":"captured-model","messages":[{"role":"user","content":"ping"}]}'
# capture headers include x-goog-api-key: CANARY_X_GOOG_API_KEY
```
