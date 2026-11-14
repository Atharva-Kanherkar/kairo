# 127, LiteLLM JSON-Defined OpenAI Providers Drop Tools by Default on Unmapped Models

- **Upstream**: [BerriAI/litellm#48145](https://github.com/BerriAI/litellm/issues/48145), open as of 2026-11-01; PR [BerriAI/litellm#48191](https://github.com/BerriAI/litellm/pull/48191)
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.llms.openai_like.dynamic_config`

## Bug or not

- **Is the expected behavior really the spec?** Yes. OpenAI-compatible providers adhere to the standard OpenAI chat completions specification which accepts `tools` and `tool_choice`. Python-defined OpenAI providers (such as `OpenAIGPTConfig` and `OpenrouterConfig`) preserve tool parameters for all models regardless of whether the model is in LiteLLM's internal cost catalog. LiteLLM's documentation specifies that dynamic JSON providers are OpenAI-compatible endpoints.
- **Have maintainers already ruled on it?** Yes. Acknowledged in open issue #48145 and companion PR #48191 (`fix(openai_like): allow tool params for unmapped models on JSON providers (#48145)`). Maintainers confirmed that unmapped models on OpenAI-compatible JSON providers should default to preserving tool parameters just as Python-defined providers do.
- **Is the trigger supported usage?** Yes. Calling `litellm.completion()` with a model routed through a JSON provider (such as `nano-gpt/z-ai/glm-5.3-flash`, `scaleway/...`, or `chutes/...`) and passing `tools=[...]` is documented, standard usage.
- **Is a real boundary crossed?** Yes. Protocol and tool capability contract boundary. The gateway silently drops user-specified tool schemas or rejects supported parameters, altering the API contract between the calling agent and the upstream provider.
- **What fix would a maintainer ship?** In `litellm/llms/openai_like/dynamic_config.py`, default `supports_function_calling` to `True` for unmapped models on OpenAI-compatible JSON providers unless `provider_info` explicitly sets `supports_function_calling: False`.
- **Classification**: `bug`

## What breaks

When an agent or client calls LiteLLM's chat completion endpoint using any of the 36 JSON-defined OpenAI-compatible providers (`nano-gpt`, `scaleway`, `chutes`, etc.) with a model that is not listed in `model_prices_and_context_window.json`:
1. `dynamic_config.py:get_supported_openai_params()` queries `supports_function_calling(model=model, custom_llm_provider=provider.slug)`.
2. Because the model is unmapped in the static catalog, `supports_function_calling()` returns `False`.
3. `get_supported_openai_params()` strips `"tools"`, `"tool_choice"`, `"function_call"`, `"functions"`, and `"parallel_tool_calls"` from `supported_params`.
4. Consequence under default settings (`drop_params=False`):
   LiteLLM throws `UnsupportedParamsError: <provider> does not support parameters: ['tools', 'tool_choice'], for model=<model>`. The agent loop crashes.
5. Consequence with `drop_params=True` (the setting recommended by LiteLLM docs for coding agents):
   LiteLLM silently strips the `tools` and `tool_choice` arrays from the outbound request! The upstream model receives the prompt ("Use get_weather tool") without any tools defined. The model is unable to call the tool, hallucinating text or apologizing to the user, completely breaking agent workflows.

## Wire evidence

- `bug-unsupported-params-error.json`: Captured exception details when `drop_params=False`, showing `tools` and `tool_choice` rejected as unsupported.
- `bug-outbound-tools-stripped.json`: Outbound HTTP body sent to upstream mock server when `drop_params=True`, showing `tools` and `tool_choice` completely omitted from outbound keys `['model', 'messages']`.
- `control-outbound-openai-route-tools-preserved.json`: Outbound HTTP body for Python-defined `openai/` route, preserving `tools` and `tool_choice`.
- `control-outbound-mapped-model-tools-preserved.json`: Outbound HTTP body when the model has `supports_function_calling: True` in the catalog, preserving `tools` and `tool_choice`.
- `control-dynamic-config-fixed-tools-preserved.json`: Verification of PR #48191 default-to-true behavior preserving tools for unmapped models.
- `results.json`: 5 of 5 runs producing identical results across all 5 conditions.

## Root cause (if found)

In `litellm/llms/openai_like/dynamic_config.py`, lines 104-118:
```python
_supports_fc: Final = supports_function_calling(
    model=model, custom_llm_provider=provider.slug
)

if not _supports_fc:
    tool_params: Final = [
        "tools",
        "tool_choice",
        "function_call",
        "functions",
        "parallel_tool_calls",
    ]
    supported_params = [
        param for param in supported_params if param not in tool_params
    ]
```
`supports_function_calling` defaults to `False` for any model not explicitly registered in `model_prices_and_context_window.json`, causing dynamic JSON providers to disable function calling for all newly released or custom models.

## Test

- Invariant checker: `openai_compatible_unmapped_model_tools_preserved` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_json_provider_unmapped_model_unsupported_params_error_wire`
  * `litellm_json_provider_unmapped_model_tools_stripped_wire`
  * `litellm_json_provider_openai_route_tools_preserved_control_wire`
  * `litellm_json_provider_mapped_model_tools_preserved_control_wire`
  * `litellm_json_provider_dynamic_config_fixed_tools_preserved_control_wire`
  * `litellm_json_provider_unmapped_model_tools_matrix_5_runs_reproduce_consistently`
