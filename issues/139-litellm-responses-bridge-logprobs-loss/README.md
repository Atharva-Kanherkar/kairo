# 139, LiteLLM Responses Bridge Drops Logprobs Across Chat Completions Bridge

- **Upstream**: Open PR [BerriAI/litellm#49536](https://github.com/BerriAI/litellm/pull/49536) (`fix(responses): carry logprobs across the chat completions bridge`)
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-02, macOS, Python 3.12, module `litellm.responses.litellm_completion_transformation`

## Bug or not

- **Is the expected behavior really the spec?** Yes. The OpenAI `/v1/responses` API specification allows callers to request token probabilities via `top_logprobs`, returning `output_text.logprobs`. When LiteLLM bridges a Responses API request to a chat completions provider, it must request chat logprobs and propagate returned token logprobs back to `output_text.logprobs`.
- **Have maintainers already ruled on it?** Yes. PR #49536 authored by Mateo Wang documents that before this fix, the bridge sent `top_logprobs` without `logprobs`, triggering HTTP 400 Bad Request errors at OpenAI and Vertex Gemini, and discarded `choice.logprobs` on return translation.
- **Is the trigger supported usage?** Yes. Calling `/v1/responses` with `top_logprobs` for models backed by chat completions (such as Vertex Gemini or OpenAI models).
- **Is a real boundary crossed?** Yes. Cross-dialect translation wire boundary. Requests with `top_logprobs` fail with HTTP 400 at upstream providers, or silently drop token logprobs on return, depriving downstream consumer applications of token probabilities.
- **What fix would a maintainer ship?** In `litellm/responses/litellm_completion_transformation/transformation.py`, bridge `top_logprobs` with `logprobs: True` in outbound chat requests, and map `choices[].logprobs` into `output_text.logprobs` on return translation.
- **Classification**: `bug`

## What breaks

1. Machine learning pipelines, evals, and uncertainty estimation systems that query LiteLLM's `/v1/responses` endpoint with `top_logprobs` receive HTTP 400 Bad Request rejections (`'top_logprobs' is only allowed when 'logprobs' is enabled`).
2. Even when a provider returns token logprobs in `choices[0].logprobs`, unpatched LiteLLM discards them during return translation, returning empty `output_text` without `logprobs`.
3. Downstream consumer agents and calibration systems relying on logprobs to gauge model confidence or compute entropy receive null logprobs, breaking quality and safety filters.

## Evidence

- `bug-responses-bridge-logprobs-dropped.json`: Captured Responses API translation showing unpatched LiteLLM dropping `top_logprobs`/`logprobs` outbound and omitting `logprobs` from `output_text` despite the provider returning token logprobs.
- `control-fixed-responses-bridge-logprobs-preserved.json`: Captured Responses API translation under PR #49536 showing `top_logprobs: 2` and `logprobs: true` forwarded, and `output_text.logprobs` populated.
- `control-responses-without-logprobs.json`: Control verifying that unrequested logprobs are omitted cleanly without spurious fields.
- `control-provider-validation-top-logprobs-requires-logprobs.json`: Control verifying provider API specification rejecting `top_logprobs` without `logprobs: true`.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause

In `litellm/responses/litellm_completion_transformation/transformation.py`:
1. `LiteLLMCompletionResponsesConfig.supported_openai_params` omitted `"top_logprobs"`.
2. `_transform_request` did not map `responses_api_request.get("top_logprobs")` or set `logprobs: True` on the outbound chat completions request.
3. `_message_output_content` and `_transform_chat_message_to_response_output_text` did not extract `choice.logprobs` or pass them into `OutputText`, discarding token probabilities returned by upstream providers.

The upstream fix in PR #49536 introduces `output_text_logprobs` extraction, passes `choice.logprobs` to `OutputText`, and bridges `top_logprobs` alongside `logprobs: True`.

## Test

- Invariant checker: `responses_bridge_logprobs_preserved` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_responses_bridge_logprobs_dropped_reproduction`
  * `litellm_fixed_responses_bridge_logprobs_preserved_control`
  * `litellm_responses_without_logprobs_control`
  * `litellm_provider_validation_top_logprobs_requires_logprobs_control`
  * `litellm_responses_bridge_logprobs_matrix_5_runs_reproduce_consistently`
