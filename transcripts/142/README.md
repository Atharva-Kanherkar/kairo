# Transcript 142: LiteLLM Gemini top_logprobs Translation Defects

## Overview

This transcript records wire evidence reproducing two defects in LiteLLM parameter translation for Google AI Studio and Vertex AI Gemini models (upstream PR [BerriAI/litellm#49514](https://github.com/BerriAI/litellm/pull/49514)):

1. In `GoogleAIStudioGeminiConfig.get_supported_openai_params`, `"top_logprobs"` is omitted from the supported parameters list. When callers send `/v1/chat/completions` or `/v1/responses` to `gemini/...` models specifying `top_logprobs`, LiteLLM immediately raises `UnsupportedParamsError: gemini does not support parameters: ['top_logprobs']` with HTTP 400.
2. In `VertexGeminiConfig.map_openai_params`, when `top_logprobs` is passed without `logprobs: true` (standard behavior in OpenAI Responses API `/v1/responses` or client SDKs that specify only the candidate count), LiteLLM maps `optional_params["logprobs"] = value` but fails to set `optional_params["responseLogprobs"] = True`. When forwarded to Google AI Studio or Vertex AI (`:generateContent`), the upstream provider returns HTTP 400: `logprobs is not supported without setting response_logprobs to be true`.

Under upstream PR #49514:
- `top_logprobs` is added to `GoogleAIStudioGeminiConfig.get_supported_openai_params`.
- `VertexGeminiConfig.map_openai_params` sets `optional_params["responseLogprobs"] = True` whenever `top_logprobs` is present.

## Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49514](https://github.com/BerriAI/litellm/pull/49514)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-02

## Differential Control Ladder

1. **Defect 1 (Chat completions with top_logprobs rejected by Google AI Studio)**:
   - Input: POST `/v1/chat/completions` with `logprobs: true, top_logprobs: 3` to `gemini/gemini-2.5-flash`.
   - Result: Gateway rejects with HTTP 400 `UnsupportedParamsError: gemini does not support parameters: ['top_logprobs']`.
2. **Defect 2 (top_logprobs mapped without responseLogprobs)**:
   - Input: Request with `top_logprobs: 3` without explicit `logprobs: true`.
   - Result: Forwarded wire payload contains `generationConfig.logprobs = 3` but omits `generationConfig.responseLogprobs: true`. Upstream Google API rejects with HTTP 400 `logprobs is not supported without setting response_logprobs to be true`.
3. **Control 1 (Patched Google AI Studio config accepts top_logprobs)**:
   - Input: Request with `logprobs: true, top_logprobs: 3`.
   - Result: Forwarded wire payload carries `generationConfig.responseLogprobs: true` and `generationConfig.logprobs: 3`. Upstream returns HTTP 200.
4. **Control 2 (Patched mapper sets responseLogprobs on top_logprobs)**:
   - Input: Request with `top_logprobs: 3` alone.
   - Result: Forwarded wire payload carries `generationConfig.responseLogprobs: true` and `generationConfig.logprobs: 3`.
5. **Control 3 (Baseline request without logprobs)**:
   - Input: Request with standard messages, no logprob parameters.
   - Result: Forwarded wire payload contains clean `contents` with neither `responseLogprobs` nor `logprobs` in `generationConfig`.
6. **Control 4 (Logprobs only)**:
   - Input: Request with `logprobs: true` only.
   - Result: Forwarded wire payload contains `generationConfig.responseLogprobs: true` without `generationConfig.logprobs` count.

## Artifacts in `wire/`

- `defect-chat-top-logprobs-unsupported.json`: Captured 400 UnsupportedParamsError from gateway.
- `defect-top-logprobs-missing-response-logprobs.json`: Captured forwarded request containing `logprobs: 3` without `responseLogprobs: true` and upstream 400 rejection.
- `control-chat-top-logprobs-patched.json`: Captured forwarded request with both fields and HTTP 200 gateway response.
- `control-top-logprobs-sets-response-logprobs.json`: Captured forwarded request where `top_logprobs` alone properly sets `responseLogprobs: true`.
- `control-baseline-no-logprobs.json`: Captured baseline request without logprob fields.
- `control-logprobs-only.json`: Captured request with `logprobs: true` only.
- `results.json`: 5 of 5 runs producing identical results across all conditions.
