# 117 transcripts

Sanitized wire evidence for LiteLLM streaming silently dropping OpenRouter
`reasoning_details`-only chunks (cryptographic thought signatures and encrypted
reasoning blobs), causing next-turn assistant message replay failure.

- Target: LiteLLM `v1.106.6` / `1.108.0-dev`, commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-31)
- Client: `httpx` 0.28.1, Python 3.12.13, macOS arm64
- Endpoint: `POST /v1/chat/completions` (streaming mode `stream: true`)
- Provider: `openrouter/anthropic/claude-sonnet-4.5`
- Upstream server: deterministic local mock OpenRouter completions server on `127.0.0.1:19996`
- Reproduction date: 2026-11-01

## Layout

| Path | Contents |
|---|---|
| `rig/reproduce.py` | Starts mock OpenRouter upstream and LiteLLM proxy, executes 5 runs across 4 differential conditions, records results |
| `wire/direct-upstream-stream-request.json` | Request payload sent to direct upstream with `stream: true` |
| `wire/direct-upstream-stream-response.txt` | Raw SSE stream from direct upstream preserving the signature-only delta chunk |
| `wire/litellm-proxy-stream-request.json` | Request payload sent to LiteLLM proxy with `stream: true` |
| `wire/litellm-proxy-stream-response.txt` | Raw SSE stream from LiteLLM proxy showing that the signature delta chunk was dropped (the defect) |
| `wire/next-turn-with-signature-request.json` | Request replaying assistant turn with thought signature intact |
| `wire/next-turn-with-signature-response.json` | Response from upstream accepting the turn with signature (HTTP 200 OK) |
| `wire/next-turn-missing-signature-request.json` | Request replaying assistant turn without signature (as rebuilt from LiteLLM stream) |
| `wire/next-turn-missing-signature-response.json` | Response from upstream rejecting the turn without signature (HTTP 400 Bad Request) |
| `wire/results.json` | 5 of 5 runs summary with target commit and per-run validation flags |

## Results

Across 5 of 5 identical runs:

| Step | Target Endpoint | Stream chunk / request | Signature emitted | Status | Result |
|---|---|---|---|---|---|
| Direct Upstream | `/api/v1/chat/completions` | Stream chunk with `reasoning_details.signature` | True | 200 OK | Control pass |
| LiteLLM Proxy | `/v1/chat/completions` | Stream chunk with `reasoning_details.signature` | False (dropped) | 200 OK | Defect confirmed |
| Direct Upstream | Next-turn follow-up | Assistant history with signature intact | N/A | 200 OK | Control pass |
| Direct Upstream | Next-turn follow-up | Assistant history missing signature | N/A | 400 Bad Request | Defect impact confirmed |

## Reproduce

```bash
cd /Users/atharva/kairo-117
DATABASE_URL="postgresql://atharva@localhost:5432/litellm_kairo" python transcripts/117/rig/reproduce.py
```
