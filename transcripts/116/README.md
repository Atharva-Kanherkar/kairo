# 116 transcripts

Sanitized wire evidence for LiteLLM single-server MCP route masking upstream
expired OAuth token 401 as HTTP 200 OK with empty `tools: []`.

- Target: LiteLLM `v1.106.6` / `1.108.0-dev`, commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-31)
- Client: `httpx` 0.28.1, Python 3.12.13, macOS arm64
- MCP Transport: Streamable HTTP / Server-Sent Events (SSE)
- Target Route: `POST /test_server/mcp`
- Target Configuration: `auth_type: "true_passthrough"`
- Upstream server: deterministic local mock MCP server on `127.0.0.1:19998/mcp`
- Reproduction date: 2026-11-01

## Layout

| Path | Contents |
|---|---|
| `rig/reproduce.py` | Starts mock MCP upstream and LiteLLM proxy, executes 5 runs across 4 differential conditions, records results |
| `wire/direct-upstream-valid-request.json` | Request payload for direct upstream `tools/list` with valid token |
| `wire/direct-upstream-valid-response.json` | Response from direct upstream returning HTTP 200 with available tools |
| `wire/direct-upstream-expired-request.json` | Request payload for direct upstream `tools/list` with expired token |
| `wire/direct-upstream-expired-response.json` | Response from direct upstream returning HTTP 401 and `WWW-Authenticate` header |
| `wire/litellm-valid-request.json` | Request payload to LiteLLM `/test_server/mcp` with valid token |
| `wire/litellm-valid-response.json` | Parsed SSE response from LiteLLM returning HTTP 200 with tools |
| `wire/litellm-expired-request.json` | Request payload to LiteLLM `/test_server/mcp` with expired token |
| `wire/litellm-expired-response.json` | Parsed SSE response from LiteLLM returning HTTP 200 with empty `tools: []` (the defect) |
| `wire/litellm-expired-raw.txt` | Raw HTTP response headers and SSE stream showing HTTP 200 OK and no `WWW-Authenticate` header |
| `wire/results.json` | 5 of 5 runs summary with target commit and per-run status codes |

## Results

Across 5 of 5 identical runs:

| Step | Target Endpoint | Token state | Expected status | Observed status | Tools returned | Challenge header | Result |
|---|---|---|---|---|---|---|---|
| Direct Upstream | Direct `/mcp` | Valid token | 200 OK | 200 OK | 1 tool | None | Control pass |
| Direct Upstream | Direct `/mcp` | Expired token | 401 Unauthorized | 401 Unauthorized | 0 tools | Present | Control pass |
| LiteLLM Proxy | `/test_server/mcp` | Valid token | 200 OK | 200 OK | 1 tool | None | Control pass |
| LiteLLM Proxy | `/test_server/mcp` | Expired token | 401 Unauthorized | 200 OK | 0 tools (`[]`) | Missing | Defect confirmed |

## Reproduce

```bash
cd /Users/atharva/kairo-116
DATABASE_URL="postgresql://atharva@localhost:5432/litellm_kairo" python transcripts/116/rig/reproduce.py
```
