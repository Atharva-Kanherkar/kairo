# 140, LiteLLM Overwrites OpenRouter Served Model in Stream Chunks with Requested Router Alias

- **Upstream**: Merged PR [BerriAI/litellm#49498](https://github.com/BerriAI/litellm/pull/49498) (`fix(streaming): preserve served models in OpenRouter streams`), resolving issue [BerriAI/litellm#46095](https://github.com/BerriAI/litellm/issues/46095)
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a`)
- **Reproduced**: 2026-11-02, macOS, Python 3.12, module `litellm.litellm_core_utils.streaming_handler`

## Bug or not

- **Is the expected behavior really the spec?** Yes. When querying an OpenRouter meta-router alias (such as `openrouter/auto`), OpenRouter selects an underlying model (e.g. `openai/gpt-4o-mini` or `deepseek/deepseek-v4.1-flash`) and returns the actual served model identity in the `model` property of each raw SSE event stream chunk. LiteLLM already preserves this behavior on non-streaming calls (`stream=False`), where `response.model` contains the served model. The streaming wrapper must preserve the provider-reported model rather than overwriting it with the requested alias.
- **Have maintainers already ruled on it?** Yes. Issue #46095 was confirmed as a bug and resolved by maintainer tin-berri in merged PR #49498, noting that unpatched LiteLLM overwrote `model_response.model = self.model` on every chunk.
- **Is the trigger supported usage?** Yes. Calling `litellm.completion(model="openrouter/auto", messages=[...], stream=True)` with standard streaming options.
- **Is a real boundary crossed?** Yes. Gateway-to-client streaming translation boundary. LiteLLM strips the provider-reported served model from all stream chunks and the assembled completion object, replacing it with the router alias `"auto"`.
- **What fix would a maintainer ship?** In `litellm/litellm_core_utils/streaming_handler.py`, assign `model_response.model = self._served_openrouter_model() or self.model` to preserve the provider-reported served model on OpenRouter stream chunks.
- **Classification**: `bug`

## What breaks

1. Downstream consumers and observability pipelines: When streaming requests to `openrouter/auto`, all emitted chunks carry `chunk.model == "auto"` instead of the model OpenRouter actually executed (e.g. `openai/gpt-4o-mini`).
2. Cost calculation and billing: `litellm.stream_chunk_builder()` constructs an assembled response where `assembled.model == "auto"`. Passing this assembled response or model to `litellm.completion_cost()` crashes with `BadRequestError: LLM Provider NOT provided. Pass in the LLM provider you are trying to call. You passed model=auto` or fails with an unmapped pricing lookup, preventing users and gateways from tracking real model costs.
3. Observability and audit logging: Monitoring integrations (Datadog, Langfuse, OpenTelemetry, Prometheus) attribute latency, token throughput, and errors to `"auto"` rather than the underlying served model, corrupting operational analytics and compliance logs.
4. Protocol inconsistency: Non-streaming completion (`stream=False`) correctly returns `response.model == "openai/gpt-4o-mini"`, creating an behavioral split where enabling streaming silently destroys model metadata.

## Wire evidence

- `bug-stream-served-model-lost.json`: Wire capture from unpatched LiteLLM v1.109.0 showing raw OpenRouter chunks delivering `model: "openai/gpt-4o-mini"`, but emitted chunks and `stream_chunk_builder()` assembled response reporting `model: "auto"`.
- `control-fixed-stream-served-model-preserved.json`: Wire capture under PR #49498 patched logic showing emitted chunks and assembled response preserving `model: "openai/gpt-4o-mini"`.
- `control-direct-stream-model.json`: Control capture verifying that direct model requests (`openrouter/openai/gpt-4o-mini`) preserve model identity across stream chunks.
- `control-nonstreaming-served-model-preserved.json`: Control capture verifying that non-streaming completion (`stream=False`) natively preserves `response.model == "openai/gpt-4o-mini"`.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause

In `litellm/litellm_core_utils/streaming_handler.py`:
1. `CustomStreamWrapper` correctly extracts `_provider_response_model` from the incoming chunk into hidden parameters, but in `chunk_creator` (line 1643 of v1.109.0), it unconditionally executed:
   ```python
   model_response.model = self.model
   ```
2. While Azure and Azure AI had special handling to preserve `self.model` from the original chunk, OpenRouter was omitted. Consequently, `model_response.model` was overwritten with `"auto"` on every chunk.
3. In `stream_chunk_builder_utils.py`, `stream_chunk_builder()` reads `model = chunks[0]["model"]`, inheriting `"auto"` and permanently corrupting the reconstructed completion response.

PR #49498 fixes this by introducing `_served_openrouter_model()` and assigning:
```python
model_response.model = self._served_openrouter_model() or self.model
```

## Test

- Invariant checker: `openrouter_stream_served_model_preserved` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_openrouter_stream_served_model_dropped_reproduction`
  * `litellm_fixed_openrouter_stream_served_model_preserved_control`
  * `litellm_openrouter_direct_stream_model_control`
  * `litellm_openrouter_nonstreaming_served_model_preserved_control`
  * `litellm_openrouter_stream_served_model_matrix_5_runs_reproduce_consistently`
