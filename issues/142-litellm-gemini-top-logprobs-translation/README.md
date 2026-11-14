# 142, LiteLLM Rejects top_logprobs on Google AI Studio Gemini Models and Drops responseLogprobs on Vertex Translation

- **Upstream**: Open PR [BerriAI/litellm#49514](https://github.com/BerriAI/litellm/pull/49514) (`fix(gemini): accept top_logprobs on Google AI Studio models`) by `mateo-berri`
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-02, macOS, Python 3.12, modules `litellm.llms.gemini.chat.transformation` and `litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini`

## Bug or not

- **Is the expected behavior really the spec?** Yes. Both OpenAI chat completions and OpenAI responses protocols support requesting token logprobs along with the candidate count (`top_logprobs`). Upstream Google AI Studio and Vertex AI Gemini models support `generationConfig.logprobs` (the candidate count) alongside `generationConfig.responseLogprobs: true`. LiteLLM's `VertexGeminiConfig` already listed `top_logprobs` as supported, but `GoogleAIStudioGeminiConfig` omitted it. Furthermore, the Google GenAI spec mandates that whenever `generationConfig.logprobs` is sent, `generationConfig.responseLogprobs` must be set to `true`.
- **Have maintainers already ruled on it?** Yes. LiteLLM maintainer mateo-berri submitted PR #49514 with classification `🐛 Bug Fix`, stating: "Chat completions and Responses requests to Google AI Studio models (gemini/...) that set top_logprobs were rejected with a 400 'gemini does not support parameters: ['top_logprobs']' ... and /v1/responses with top_logprobs built a Gemini body with logprobs but no responseLogprobs, which Gemini and Vertex both reject with their own 400."
- **Is the trigger supported usage?** Yes. Standard OpenAI chat completion requests with `logprobs: true, top_logprobs: 3` and OpenAI responses requests with `top_logprobs: 3` to Google AI Studio or Vertex AI Gemini models.
- **Is a real boundary crossed?** Yes. Gateway parameter validation and provider wire translation boundaries. The gateway rejects valid client requests upfront, and forwards invalid generation configurations to upstream Google endpoints causing upstream HTTP 400 errors.
- **What fix would a maintainer ship?** Add `"top_logprobs"` to `GoogleAIStudioGeminiConfig.get_supported_openai_params` and set `optional_params["responseLogprobs"] = True` in `VertexGeminiConfig.map_openai_params` when mapping `top_logprobs`.
- **Classification**: `bug`

## What breaks

1. Premature request rejection on Google AI Studio models: Any client sending `top_logprobs` to a `gemini/...` model (such as `gemini/gemini-2.5-flash` or `gemini/gemini-1.5-pro`) receives an immediate HTTP 400 `litellm.UnsupportedParamsError: gemini does not support parameters: ['top_logprobs']` from the gateway.
2. Upstream provider rejection on responses with top_logprobs alone: Clients calling `/v1/responses` or standard SDK callers that pass `top_logprobs` without explicitly repeating `logprobs: true` result in LiteLLM creating a Gemini payload with `generationConfig: {"logprobs": 3}` lacking `responseLogprobs: true`. Upstream Google AI Studio and Vertex AI APIs return HTTP 400 `logprobs is not supported without setting response_logprobs to be true`.
3. Inconsistent routing behavior: The exact same request parameters work or fail depending on whether the model is prefixed with `vertex_ai/` or `gemini/`, breaking portability across deployments.

## Wire evidence

- `defect-chat-top-logprobs-unsupported.json`: Wire capture showing client request with `top_logprobs: 3` rejected by LiteLLM with HTTP 400 `UnsupportedParamsError`.
- `defect-top-logprobs-missing-response-logprobs.json`: Wire capture showing translated request body with `generationConfig.logprobs = 3` missing `responseLogprobs: true`, resulting in upstream provider HTTP 400.
- `control-chat-top-logprobs-patched.json`: Control capture showing PR #49514 patched gateway forwarding compliant `responseLogprobs: true` and `logprobs: 3`, returning HTTP 200.
- `control-top-logprobs-sets-response-logprobs.json`: Control capture showing `top_logprobs` alone properly setting `responseLogprobs: true` under the patched mapper.
- `control-baseline-no-logprobs.json`: Control capture confirming standard requests without logprobs omit both fields.
- `control-logprobs-only.json`: Control capture confirming requests with only `logprobs: true` omit `logprobs` count while setting `responseLogprobs: true`.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause

Two root causes in LiteLLM's Gemini transformation layer:

1. `litellm/llms/gemini/chat/transformation.py`:
   `GoogleAIStudioGeminiConfig` overrides `get_supported_openai_params` with a static list of supported parameters. It included `"logprobs"` but omitted `"top_logprobs"`, causing `litellm.validate_environment()` to treat `top_logprobs` as unsupported on `gemini/` models.
2. `litellm/llms/vertex_ai/gemini/vertex_and_google_ai_studio_gemini.py`:
   In `VertexGeminiConfig.map_openai_params`:
   ```python
   elif param == "logprobs":
       optional_params["responseLogprobs"] = value
   elif param == "top_logprobs":
       optional_params["logprobs"] = value
   ```
   When `top_logprobs` was present without `logprobs`, `optional_params["responseLogprobs"]` was never set. Because Google GenAI APIs reject `generationConfig.logprobs` when `responseLogprobs` is absent or false, outgoing calls failed upstream.

PR #49514 resolves both issues by adding `"top_logprobs"` to `GoogleAIStudioGeminiConfig.get_supported_openai_params` and adding `optional_params["responseLogprobs"] = True` to the `top_logprobs` branch of `map_openai_params`.

## Test

- Invariant checker: `gemini_logprobs_params_mapped` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_gemini_chat_top_logprobs_unsupported_reproduction`
  * `litellm_gemini_top_logprobs_missing_response_logprobs_reproduction`
  * `litellm_gemini_chat_top_logprobs_patched_control`
  * `litellm_gemini_top_logprobs_sets_response_logprobs_control`
  * `litellm_gemini_baseline_no_logprobs_control`
  * `litellm_gemini_logprobs_only_control`
  * `litellm_gemini_logprobs_matrix_5_runs_reproduce_consistently`
