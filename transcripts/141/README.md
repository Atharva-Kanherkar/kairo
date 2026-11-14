# Transcript 141: LiteLLM Cohere v2 Non-Streaming Finish Reason Loss

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's non-streaming Cohere v2 chat response transformation omits mapping the provider's `finish_reason`, defaulting `choices[0].finish_reason` to `"stop"` for all responses (open PR [BerriAI/litellm#49529](https://github.com/BerriAI/litellm/pull/49529), Linear ticket LIT-9731).

When a client queries a Cohere v2 model (e.g. `cohere_chat/v2/command-a-03-2025`):
1. Upstream Cohere v2 returns JSON carrying `finish_reason: "TOOL_CALL"`, `"MAX_TOKENS"`, `"COMPLETE"`, or `"STOP_SEQUENCE"`.
2. In unpatched LiteLLM (`v1.109.0`), `CohereV2ChatConfig.transform_response()` never assigns `model_response.choices[0].finish_reason`, leaving it at the default `"stop"`.
3. Agent loops that check `choices[0].finish_reason == "tool_calls"` see `"stop"` and terminate prematurely without executing the generated tools.
4. Truncated responses with `finish_reason: "MAX_TOKENS"` return `"stop"` instead of `"length"`, hiding truncation from callers.
5. Under PR #49529, `model_response.choices[0].finish_reason` is mapped via `map_finish_reason()`, restoring `"tool_calls"` on `TOOL_CALL` and `"length"` on `MAX_TOKENS`.

## Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49529](https://github.com/BerriAI/litellm/pull/49529)
- Upstream Ticket: LIT-9731
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-02

## Differential Control Ladder

1. **Defect 1 (Tool Call Finish Reason)**:
   - Input: Non-streaming request with tools configured to `cohere_chat/v2/command-a-03-2025`.
   - Upstream: Cohere v2 returns `finish_reason: "TOOL_CALL"` and tool calls.
   - Result: LiteLLM returns `choices[0].finish_reason == "stop"` instead of `"tool_calls"`.
2. **Defect 2 (Max Tokens Finish Reason)**:
   - Input: Non-streaming request to `cohere_chat/v2/command-a-03-2025`.
   - Upstream: Cohere v2 returns `finish_reason: "MAX_TOKENS"`.
   - Result: LiteLLM returns `choices[0].finish_reason == "stop"` instead of `"length"`.
3. **Control 1 (Fixed Tool Call under PR #49529)**:
   - Input: Non-streaming request with tools configured.
   - Result: LiteLLM returns `choices[0].finish_reason == "tool_calls"`.
4. **Control 2 (Fixed Max Tokens under PR #49529)**:
   - Input: Non-streaming request with token truncation.
   - Result: LiteLLM returns `choices[0].finish_reason == "length"`.
5. **Control 3 (Complete Generation)**:
   - Input: Non-streaming request completing naturally.
   - Upstream: Cohere v2 returns `finish_reason: "COMPLETE"`.
   - Result: LiteLLM returns `choices[0].finish_reason == "stop"`.
6. **Control 4 (Stop Sequence)**:
   - Input: Non-streaming request hitting custom stop token.
   - Upstream: Cohere v2 returns `finish_reason: "STOP_SEQUENCE"`.
   - Result: LiteLLM returns `choices[0].finish_reason == "stop"`.

## Artifacts in `wire/`

- `bug-cohere-v2-tool-call-finish-reason-stop.json`: Captured tool call response showing `finish_reason: "stop"` despite tool calls.
- `bug-cohere-v2-max-tokens-finish-reason-stop.json`: Captured truncated response showing `finish_reason: "stop"` instead of `"length"`.
- `control-fixed-cohere-v2-tool-calls.json`: Captured fixed tool call response showing `finish_reason: "tool_calls"`.
- `control-fixed-cohere-v2-max-tokens.json`: Captured fixed truncated response showing `finish_reason: "length"`.
- `control-cohere-v2-complete.json`: Captured natural completion showing `finish_reason: "stop"`.
- `results.json`: 5 of 5 runs producing identical results across all conditions.
