# 130, LiteLLM MiniMax Provider Silently Drops Top-Level Reasoning Effort

- **Upstream**: [BerriAI/litellm#49574](https://github.com/BerriAI/litellm/issues/49574), open PR [BerriAI/litellm#49583](https://github.com/BerriAI/litellm/pull/49583), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.llms.minimax.chat.transformation`

## Bug or not

- **Is the expected behavior really the spec?** Yes. OpenAI Chat Completions specification defines `reasoning_effort` (`low|medium|high|max`) for reasoning models. MiniMax M3.1-Flash natively accepts effort levels. When callers request reasoning models through LiteLLM, the gateway must forward supported reasoning parameters to the upstream provider rather than silently stripping them.
- **Have maintainers already ruled on it?** Yes. Acknowledged in open PR #49583 (`fix(minimax): forward reasoning_effort instead of dropping it`). Maintainers and contributors confirmed that MiniMax models natively accept reasoning effort levels and that stripping them prevents callers from tuning reasoning depth.
- **Is the trigger supported usage?** Yes. Passing `reasoning_effort: "low"` or `"high"` at the top level of a chat completion request to a MiniMax model (e.g. `minimax/MiniMax-M3.1-Flash`) is documented, standard usage.
- **Is a real boundary crossed?** Yes. Protocol and configuration fidelity boundary. The gateway silently purges user-specified reasoning constraints from the outbound request, returning default token generation without warning.
- **What fix would a maintainer ship?** Add `"reasoning_effort"` to `additional_params` in `MinimaxChatConfig.get_supported_openai_params()` in `litellm/llms/minimax/chat/transformation.py`.
- **Classification**: `bug`

## What breaks

When a developer or agent loop configures reasoning effort on MiniMax models:
1. The caller sends a request with `reasoning_effort: "low"` to reduce latency and cost, or `reasoning_effort: "high"` for intricate mathematical/coding tasks.
2. In `litellm/llms/minimax/chat/transformation.py`, `MinimaxChatConfig.get_supported_openai_params` only lists `reasoning_split` and `thinking`.
3. `MinimaxChatConfig.map_openai_params` filters out `reasoning_effort`, purging it from the outbound payload.
4. MiniMax receives the request without any reasoning effort instruction.
5. The upstream provider falls back to default reasoning tokens, generating identical token usage and latency regardless of whether `low` or `high` was specified.
6. The caller experiences silent configuration loss without error messages or warnings.

## Wire evidence

- `bug-minimax-reasoning-effort-dropped.json`: Captured mapping output showing `reasoning_effort` omitted from outbound request parameters.
- `control-fixed-minimax-reasoning-effort-preserved.json`: Captured mapping output for fixed config showing `reasoning_effort: "low"` preserved.
- `control-openai-route-reasoning-effort-preserved.json`: Captured mapping output for OpenAI o-series route confirming standard handling.
- `control-high-effort-preserved.json`: Captured mapping output for high effort level confirming multi-level support.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause (if found)

In `litellm/llms/minimax/chat/transformation.py`, lines 81-88:
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
`additional_params` only includes `"reasoning_split"`, omitting `"reasoning_effort"`. Consequently, any request passing `reasoning_effort` is filtered out during parameter mapping.

## Test

- Invariant checker: `minimax_reasoning_effort_preserved` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_minimax_drops_reasoning_effort_wire_reproduction`
  * `litellm_minimax_fixed_reasoning_effort_preserved_control`
  * `litellm_minimax_oseries_route_reasoning_effort_control`
  * `litellm_minimax_high_effort_preserved_control`
  * `litellm_minimax_matrix_5_runs_reproduce_consistently`
