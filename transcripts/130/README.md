# Transcript 130: LiteLLM MiniMax Provider Drops Top-Level Reasoning Effort

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM silently drops the top-level `reasoning_effort` parameter when mapping OpenAI Chat Completions requests for MiniMax models such as `minimax/MiniMax-M3.1-Flash` (Upstream Issue [BerriAI/litellm#49574](https://github.com/BerriAI/litellm/issues/49574), PR [BerriAI/litellm#49583](https://github.com/BerriAI/litellm/pull/49583)).

In `litellm/llms/minimax/chat/transformation.py`, `MinimaxChatConfig.get_supported_openai_params` defines supported parameters:
```python
def get_supported_openai_params(self, model: str) -> list:
    base_params: Final = super().get_supported_openai_params(model=model)
    additional_params: Final = ["reasoning_split"]
    try:
        from litellm.utils import supports_reasoning
        if supports_reasoning(model=model):
            additional_params.append("thinking")
    except Exception:
        pass
    return base_params + additional_params
```

The method appends `reasoning_split` (and conditionally `thinking`), but omits `reasoning_effort`. When callers pass `reasoning_effort: "low"` or `"high"`:
- `MinimaxChatConfig.map_openai_params` filters parameters against `get_supported_openai_params`.
- Because `reasoning_effort` is not in the supported list, `map_openai_params` drops the parameter completely, returning an empty parameter dictionary `{}`.
- The outbound payload forwarded to MiniMax omits `reasoning_effort`.
- MiniMax receives no effort instruction and falls back to default reasoning tokens, rendering low and high effort requests identical in token consumption and latency.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49583](https://github.com/BerriAI/litellm/pull/49583)
- Target Upstream Issue: [BerriAI/litellm#49574](https://github.com/BerriAI/litellm/issues/49574)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case (`MinimaxChatConfig` with `reasoning_effort: "low"`)**:
   - Map parameters for `minimax/MiniMax-M3.1-Flash` with `reasoning_effort: "low"`.
   - Result: `reasoning_effort` is dropped; mapped parameters output `{}`.
2. **Control 1 (Fixed PR #49583 config)**:
   - Map parameters with `FixedMinimaxChatConfig` adding `reasoning_effort` to `additional_params`.
   - Result: `reasoning_effort: "low"` is preserved in mapped parameters.
3. **Control 2 (OpenAI o-series route)**:
   - Map `reasoning_effort: "low"` through `OpenAIOSeriesConfig`.
   - Result: `reasoning_effort: "low"` is preserved.
4. **Control 3 (High effort)**:
   - Map `reasoning_effort: "high"` with fixed config.
   - Result: `reasoning_effort: "high"` is preserved.

## Artifacts in `wire/`

- `bug-minimax-reasoning-effort-dropped.json`: Captured mapping output showing `reasoning_effort` dropped from outbound parameters.
- `control-fixed-minimax-reasoning-effort-preserved.json`: Captured mapping output for fixed config showing `reasoning_effort: "low"` preserved.
- `control-openai-route-reasoning-effort-preserved.json`: Captured mapping output for OpenAI o-series route.
- `control-high-effort-preserved.json`: Captured mapping output for high effort level.
- `results.json`: 5 of 5 runs producing identical results across all 4 conditions.
