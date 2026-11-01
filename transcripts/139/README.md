# Transcript 139: LiteLLM Responses Bridge Drops Logprobs Across Chat Bridge

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's `/v1/responses` to Chat Completions bridge omits `top_logprobs` and `logprobs` from outbound chat requests and discards `choice.logprobs` on the return hop (open PR [BerriAI/litellm#49536](https://github.com/BerriAI/litellm/pull/49536)).

When a client queries `/v1/responses` with `top_logprobs`:
1. Unpatched LiteLLM omits `top_logprobs` and `logprobs: true` in `LiteLLMCompletionResponsesConfig.transform_responses_api_request_to_chat_completion_request`.
2. When forwarded without `logprobs: true`, downstream providers (OpenAI, Vertex Gemini) reject the call with HTTP 400 Bad Request (`'top_logprobs' is only allowed when 'logprobs' is enabled`).
3. On return translation in `transform_chat_completion_response_to_responses_api_response`, unpatched LiteLLM drops `choice.logprobs`, leaving `output_text.logprobs` empty.
4. Under PR #49536, the bridge forwards `top_logprobs` alongside `logprobs: true` and maps `choice.logprobs` into `output_text.logprobs`.

## Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49536](https://github.com/BerriAI/litellm/pull/49536)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-02

## Differential Control Ladder

1. **Failing Case (Unpatched Responses Bridge)**:
   - Input: `/v1/responses` request with `top_logprobs: 2` on `vertex_ai/gemini-2.5-flash`.
   - Result: Request omits `logprobs: true`; response drops `choice.logprobs` and returns `output_text` without `logprobs`.
2. **Control 1 (Fixed Responses Bridge under PR #49536)**:
   - Input: `/v1/responses` request with `top_logprobs: 2`.
   - Result: Outbound chat request sets `top_logprobs: 2` and `logprobs: true`; response populates `output_text.logprobs`.
3. **Control 2 (Responses Request Without Logprobs)**:
   - Input: `/v1/responses` request without `top_logprobs`.
   - Result: No logprobs requested; response cleanly omits `logprobs`.
4. **Control 3 (Provider Specification Validation)**:
   - Input: Outbound chat request with `top_logprobs: 2` but missing `logprobs: true`.
   - Result: HTTP 400 Bad Request (`'top_logprobs' is only allowed when 'logprobs' is enabled`).

## Artifacts in `wire/`

- `bug-responses-bridge-logprobs-dropped.json`: Captured response showing unpatched bridge dropping token logprobs from `output_text`.
- `control-fixed-responses-bridge-logprobs-preserved.json`: Captured response showing fixed bridge propagating token logprobs into `output_text.logprobs`.
- `control-responses-without-logprobs.json`: Captured response without logprobs.
- `control-provider-validation-top-logprobs-requires-logprobs.json`: Captured provider rejection for `top_logprobs` without `logprobs: true`.
- `results.json`: 5 of 5 runs producing identical results across all conditions.
