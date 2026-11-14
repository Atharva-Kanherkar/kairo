# 119 transcripts

Sanitized wire evidence for LiteLLM native Anthropic MCP tool loop dropping the `is_error` flag
from `tool_result` content blocks when forwarding executed tool results back to Anthropic
`/v1/messages`.

- Target: LiteLLM `v1.106.6` / `1.108.0-dev`, commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-31)
- Client: `httpx` 0.28.1, Python 3.12.13, macOS arm64
- Endpoint: `POST /v1/messages` with native MCP tool execution (`server_url: "litellm_proxy/test_db"`)
- Upstream server: Deterministic local mock Anthropic server on `127.0.0.1:19996/v1/messages`
- Mock MCP server: Local JSON-RPC 2.0 MCP server on `127.0.0.1:19997/mcp`
- Reproduction date: 2026-11-01

## Layout

| Path | Contents |
|---|---|
| `rig/reproduce.py` | Starts mock Anthropic upstream, mock MCP server, and LiteLLM proxy, executes 5 runs across differential conditions |
| `wire/client-request.json` | Request payload sent by client to LiteLLM `POST /v1/messages` |
| `wire/proxy-client-response.json` | Final response received by client from LiteLLM proxy |
| `wire/anthropic-turn1-request.json` | Request received by Anthropic upstream on Turn 1 |
| `wire/anthropic-turn2-forwarded-request.json` | Request received by Anthropic upstream on Turn 2 containing executed tool result without `is_error: true` (the defect) |
| `wire/control-direct-anthropic-request.json` | Control request sent directly to Anthropic with `is_error: true` intact |
| `wire/control-direct-anthropic-response.json` | Control response from Anthropic handling the tool error |
| `wire/results.json` | 5 of 5 runs summary with target commit and per-run validation flags |

## Results

Across 5 of 5 identical runs:

| Step | Target Endpoint | Tool Call Status | Tool Result `is_error` Emitted | Result |
|---|---|---|---|---|
| Direct Anthropic Control | `/v1/messages` | Failed (Connection refused) | True (`"is_error": true`) | Control pass |
| LiteLLM Turn 2 Forwarded | `/v1/messages` | Failed (Error executing tool) | False (dropped) | Defect confirmed |

## Reproduce

```bash
cd /Users/atharva/kairo-119
DATABASE_URL="postgresql://atharva@localhost:5432/litellm_kairo" python transcripts/119/rig/reproduce.py
```
