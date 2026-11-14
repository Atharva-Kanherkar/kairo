# 118 transcripts

Sanitized wire evidence for LiteLLM `pass_through_endpoints` unconditionally stripping the
JSON-RPC 2.0 `id` field from forwarded request bodies due to an internal parameter name collision
(`types_utils.all_litellm_params`), converting RPC method calls into notifications and breaking
response correlation.

- Target: LiteLLM `v1.106.6` / `1.108.0-dev`, commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-31)
- Client: `httpx` 0.28.1, Python 3.12.13, macOS arm64
- Endpoint: `POST /mcp-passthrough` (configured in `general_settings.pass_through_endpoints`)
- Provider: Deterministic local mock JSON-RPC 2.0 server on `127.0.0.1:19992/jsonrpc`
- Reproduction date: 2026-11-01

## Layout

| Path | Contents |
|---|---|
| `rig/reproduce.py` | Starts mock JSON-RPC server and LiteLLM proxy, executes 5 runs across 4 differential conditions, records results |
| `wire/client-proxy-request.json` | Request payload sent by client to LiteLLM pass-through route containing `"id": 100` |
| `wire/upstream-received-from-proxy.json` | Body received by upstream target from LiteLLM proxy, demonstrating that `id` was stripped (the defect) |
| `wire/client-proxy-response.txt` | Response received by client from proxy: HTTP 204 No Content (upstream treated request as notification) |
| `wire/direct-upstream-request.json` | Control request sent directly to upstream target |
| `wire/direct-upstream-response.json` | Control response from direct upstream echoing `"id": 100` with HTTP 200 OK |
| `wire/control-non-colliding-request.json` | Request payload with non-colliding identifier `client_request_id` |
| `wire/control-non-colliding-forwarded.json` | Forwarded body from proxy retaining `client_request_id`, isolating the collision against `all_litellm_params` |
| `wire/results.json` | 5 of 5 runs summary with target commit and per-run validation flags |

## Results

Across 5 of 5 identical runs:

| Step | Target Endpoint | Request Payload | Upstream Received `id` | Client Response Status | Result |
|---|---|---|---|---|---|
| Direct Upstream | `/jsonrpc` | `{"jsonrpc": "2.0", "id": 100, ...}` | True (`100`) | 200 OK (`id: 100`) | Control pass |
| LiteLLM Proxy | `/mcp-passthrough` | `{"jsonrpc": "2.0", "id": 100, ...}` | False (stripped) | 204 No Content | Defect confirmed |
| LiteLLM Proxy | `/mcp-passthrough` | `{"jsonrpc": "2.0", "client_request_id": 100, ...}` | True (`100`) | 204 No Content | Collision isolated |

## Reproduce

```bash
cd /Users/atharva/kairo-118
DATABASE_URL="postgresql://atharva@localhost:5432/litellm_kairo" python transcripts/118/rig/reproduce.py
```
