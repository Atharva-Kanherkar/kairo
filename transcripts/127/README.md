# Transcript 127: LiteLLM JSON-Defined OpenAI Providers Drop Tools by Default on Unmapped Models

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM purges tool definitions from outbound chat completion requests when using any of its 36 JSON-defined OpenAI-compatible providers (`nano-gpt`, `scaleway`, `chutes`, etc.) with models not registered in its static pricing catalog (Upstream issue [#48145](https://github.com/BerriAI/litellm/issues/48145), open PR [#48191](https://github.com/BerriAI/litellm/pull/48191)).

In `litellm/llms/openai_like/dynamic_config.py`, `get_supported_openai_params()` calls:
```python
_supports_fc: Final = supports_function_calling(model=model, custom_llm_provider=provider.slug)
```
For any model absent from `model_prices_and_context_window.json`, `supports_function_calling` returns `False`. The handler then removes all tool-related parameters:
```python
tool_params: Final = ["tools", "tool_choice", "function_call", "functions", "parallel_tool_calls"]
supported_params = [param for param in supported_params if param not in tool_params]
```

When callers pass tools:
- Default `drop_params=False`: LiteLLM raises `UnsupportedParamsError: <provider> does not support parameters: ['tools', 'tool_choice'], for model=<model>`.
- `drop_params=True`: LiteLLM silently strips `tools` and `tool_choice` from the HTTP request body sent to the upstream endpoint, sending only `model` and `messages`. The model is deprived of tools and fails to execute the requested actions.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream Issue: [BerriAI/litellm#48145](https://github.com/BerriAI/litellm/issues/48145)
- Upstream PR: [BerriAI/litellm#48191](https://github.com/BerriAI/litellm/pull/48191)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case 1 (`drop_params=False`)**:
   - Send chat completion with `tools=[...]` to `nano-gpt/z-ai/glm-5.3-flash`.
   - Result: Throws `UnsupportedParamsError` naming `tools` and `tool_choice`.
2. **Failing Case 2 (`drop_params=True`)**:
   - Send chat completion with `tools=[...]` and `tool_choice="auto"`.
   - Result: Outbound HTTP body contains only `['model', 'messages']`. Both `tools` and `tool_choice` are stripped.
3. **Control 1 (Python-defined OpenAI route)**:
   - Call `model="openai/z-ai/glm-5.3-flash"`.
   - Result: Outbound HTTP body preserves `tools` and `tool_choice`.
4. **Control 2 (Catalog-mapped model)**:
   - Call model with `supports_function_calling: True` in catalog.
   - Result: Outbound HTTP body preserves `tools` and `tool_choice`.
5. **Control 3 (Fixed PR #48191 dynamic config logic)**:
   - Dynamic config defaults unmapped models on OpenAI-compatible JSON providers to preserving tools.
   - Result: Tool parameters preserved.

## Artifacts in `wire/`

- `bug-unsupported-params-error.json`: Captured exception when `drop_params=False`.
- `bug-outbound-tools-stripped.json`: Captured outbound payload when `drop_params=True` showing tools omitted.
- `control-outbound-openai-route-tools-preserved.json`: Captured outbound payload for Python-defined `openai/` route.
- `control-outbound-mapped-model-tools-preserved.json`: Captured outbound payload for catalog-mapped model.
- `control-dynamic-config-fixed-tools-preserved.json`: Output of PR #48191 resolver logic.
- `results.json`: 5 of 5 runs producing identical results across all 5 conditions.
