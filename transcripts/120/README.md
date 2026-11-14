# 120 transcripts

Sanitized wire evidence for LiteLLM Anthropic streaming guardrail translation parsing
transport chunks as complete SSE frames, silently dropping text and bypassing content
filters on fragmented chunks.

- Upstream issue: https://github.com/BerriAI/litellm/issues/49449
- Target: LiteLLM `v1.106.6` / `1.108.0-dev`, commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-31)
- Client: `httpx` 0.28.1, Python 3.12.13, macOS arm64
- Endpoint: `POST /v1/messages` with `stream: true` and post_call guardrail `litellm_content_filter`
- Upstream server: Deterministic local mock Anthropic server on `127.0.0.1:19998/v1/messages`
- Reproduction date: 2026-11-01

## Layout

| Path | Contents |
|---|---|
| `rig/reproduce.py` | Starts mock Anthropic upstream and LiteLLM proxy with ContentFilterGuardrail, executes 5 runs across differential conditions |
| `wire/client-request-control.json` | Request payload sent by client in unfragmented control case |
| `wire/proxy-response-control.json` | Control response from proxy blocking prohibited pattern with HTTP 400 |
| `wire/client-request-bug.json` | Request payload sent by client in fragmented bug case |
| `wire/proxy-response-bug.txt` | Response received by client in bug case streaming prohibited token (HTTP 200) |
| `wire/upstream-sse-stream.txt` | Raw SSE stream emitted by mock Anthropic upstream |
| `wire/results.json` | 5 of 5 runs summary with target commit and per-run validation flags |

## Results

Across 5 of 5 identical runs:

| Step | Stream Chunking | Post-Call Guardrail Behavior | Proxy HTTP Status | Prohibited Token Delivered | Result |
|---|---|---|---|---|---|
| Control | Whole SSE frames | Blocks pattern with HTTP 400 error | 400 | No | Control pass |
| Defect | Fragmented transport chunk | JSONDecodeError, text dropped, filter bypassed | 200 | Yes (`BLOCKED_SECRET_TOKEN_49449`) | Defect confirmed |

## Reproduce

```bash
cd /Users/atharva/kairo-120
python transcripts/120/rig/reproduce.py
```
