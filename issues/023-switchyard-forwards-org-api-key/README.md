# 023, Switchyard forwards `api-key` and OpenAI org/project headers the reserved list missed

- **Upstream**: no single ticket. `RESERVED_HEADERS` in
  `libsy-llm-client/src/client.rs` strips `authorization`, `x-api-key`,
  `cookie`, and hop-by-hop names. It does not strip `api-key` (Azure OpenAI's
  credential header) or `OpenAI-Organization` / `OpenAI-Project` (OpenAI
  tenant selectors). Adjacent to the documented intent of that list: "Auth
  ... are set by the backend ... a forwarded copy would either be ignored
  or conflict."
- **Tool under test**: Switchyard `switchyard-server` 0.2.0 (commit 2bef154).
- **Not a credential incident**: probes used fake canary tokens. Live OpenAI
  and Gemini error bodies were scanned for real keys. No leak. No rotation
  needed.
- **Reproduced**: 2026-08-14. Header forward 5/5 on the capture rig. Live
  OpenAI 5/5: an invalid `OpenAI-Organization` through Switchyard is HTTP
  401 `mismatched_organization`, matching the direct-OpenAI control.
  Evidence: `transcripts/023/`.

## What breaks

A Switchyard caller can send `api-key`, `OpenAI-Organization`, or
`OpenAI-Project`. Those names are not reserved, so
`forward_metadata_headers` copies them onto the upstream request.
`apply_auth` then sets `Authorization: Bearer <config>` (or Anthropic
`x-api-key`) without removing the extras.

Who that hurts:

- OpenAI deployments: a client `OpenAI-Organization` that does not match
  the admin key's org makes the upstream 401. Live, 5/5. A *valid* org id
  the same key can access would bill that org instead. `OpenAI-Project`
  is the same shape for project-scoped keys.
- Azure OpenAI deployments that authenticate with `api-key`: a client
  `api-key` is forwarded as that credential header. The mock shows it on
  the wire 5/5 while `Authorization` stays the deployment bearer. Live
  OpenAI and Gemini ignore `api-key` (HTTP 200, bearer wins), so the
  live leak is the header leaving the proxy, not an auth swap on those
  two providers.
- The reserved list *does* work for the names it includes: client
  `Authorization` and `x-api-key` are dropped. 5/5. `api-key` without
  the `x-` prefix is the hole.

NVIDIA's `SECURITY.md` asks for PSIRT mail (`psirt@nvidia.com`) rather
than a public GitHub issue for this class of report.

## Wire evidence

Three legs.

1. **Switchyard (mock OpenAI at 127.0.0.1:9999, deployment `Bearer x`)**
   - Client header `api-key=CANARY_AZURE_API_KEY` appears on the upstream
     request. `Authorization` stays `Bearer x`. 5/5.
     `transcripts/023/cap-api-key.jsonl`.
   - Client headers `OpenAI-Organization=CANARY_OPENAI_ORG` and
     `OpenAI-Project=CANARY_OPENAI_PROJECT` appear on the upstream
     request. 5/5. `transcripts/023/cap-openai-org.jsonl`.
   - Client `x-api-key=CANARY_X_API_KEY` is stripped. 5/5.
     `transcripts/023/cap-x-api-key-stripped.jsonl`.
   - Client `Authorization: Bearer CANARY_AUTHORIZATION` is stripped.
     Upstream stays `Bearer x`. 5/5.
2. **Control**
   - Same Switchyard, no extra headers: no canaries
     (`transcripts/023/cap-control.jsonl`).
   - Direct mock, no extra headers: no canaries
     (`transcripts/023/cap-direct-plain.jsonl`).
   - Direct OpenAI with the real bearer: HTTP 200. Direct OpenAI with
     `OpenAI-Organization: org-CANARYINVALIDORG`: HTTP 401
     `mismatched_organization`. Direct OpenAI with extra `api-key:
     CANARY_AZURE_API_KEY`: HTTP 200 (OpenAI uses bearer).
3. **Determinism**
   - Live Switchyard → `gpt-4o-mini` with client
     `OpenAI-Organization: org-CANARYINVALIDORG`: HTTP 401 wrapping
     `OpenAI-Organization header should match organization for API key`.
     5/5. Same request without that header: HTTP 200. 5/5.
     `transcripts/023/live-results.json`.
   - Live Switchyard → Gemini 2.5 Flash with client `api-key:
     CANARY_AZURE_API_KEY`: HTTP 200. 5/5. Google ignores `api-key`.
     `transcripts/023/live-gemini.json`. The mock is what proves the
     header still went out.

Diff, live OpenAI, every run:

| call | direct OpenAI | Switchyard |
|------|---------------|------------|
| valid configured key | 200 | 200 |
| invalid `OpenAI-Organization` | **401 mismatched_organization** | **401 mismatched_organization** |
| extra `api-key` canary | 200 (ignored) | 200 (ignored; mock shows it is still sent) |

Same-format extra observations, not frozen as 023:

- JSON body `api_key`, `api_base`, `base_url`, `extra_headers`, `headers`,
  `store`, and `user` are preserved onto the upstream JSON (unknown-field
  policy default Preserve). They do not become HTTP headers and do not
  retarget `base_url`. Live Gemini would 400 unknown fields. Unlike
  LiteLLM 020/022, Switchyard does not swap `Authorization` for those
  JSON fields.
- `/v1/stats`, `/metrics`, and `POST /v1/stats/reset` are unauthenticated
  on this server. Reset returned 200. Traffic counters only, no keys.

When the deployment *also* sets `extra_headers.api-key`, the capture
showed the config value, not the client canary (config is applied after
forward). The hole is the names `apply_auth` never writes: org/project
always, and `api-key` when the admin did not put it in extra_headers.

## Root cause (in Switchyard source)

`switchyard-server/src/lib.rs` `metadata_from_headers` stores the entire
inbound `HeaderMap`. `libsy-llm-client/src/client.rs` `send_once`:

```
forward_metadata_headers  // all non-reserved client headers
apply_extra_headers       // config
apply_auth                // Authorization Bearer or Anthropic x-api-key
```

`RESERVED_HEADERS` includes `authorization` and `x-api-key`. It does not
include `api-key`, `openai-organization`, or `openai-project`.

## Test invariants

1. A client `api-key` or `OpenAI-Organization` MUST NOT appear on the
   upstream request unless the admin opted that header in.
2. A request that did not send those headers MUST NOT grow them.
3. Client `x-api-key` and `Authorization` MUST stay stripped (the names
   the list already covers).

## Repro

```
python3 tools/capture_headers.py 9999 transcripts/023/cap-openai-org.jsonl transcripts/020/canned-openai.json
tools/switchyard/target/release/switchyard-server --config tools/switchyard-capture.toml --port 9000
curl -s localhost:9000/v1/chat/completions \
  -H 'content-type: application/json' \
  -H 'OpenAI-Organization: CANARY_OPENAI_ORG' \
  -d '{"model":"captured-model","messages":[{"role":"user","content":"ping"}]}'
# capture headers include openai-organization: CANARY_OPENAI_ORG
```
