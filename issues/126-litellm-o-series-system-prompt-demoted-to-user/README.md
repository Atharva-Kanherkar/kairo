# 126, LiteLLM o-Series System Prompt Demoted to User on o3, o4-mini, and o3-mini

- **Upstream**: [BerriAI/litellm#48731](https://github.com/BerriAI/litellm/issues/48731), open as of 2026-11-01; PR [BerriAI/litellm#48732](https://github.com/BerriAI/litellm/pull/48732)
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.llms.openai.chat.o_series_transformation`

## Bug or not

- **Is the expected behavior really the spec?** Yes. OpenAI and Azure officially support developer/system messages on all current o-series reasoning models (including `o3`, `o4-mini`, and `o3-mini`) since December 2024. LiteLLM already preserves system messages for `o1` because `supports_system_messages: true` is set for `o1` in `model_prices_and_context_window.json`.
- **Have maintainers already ruled on it?** Yes. Acknowledged in open issue #48731 and companion PR #48732 (`fix(o-series): support system messages for o3, o4-mini and o3-mini`). Maintainers confirm reasoning models support system messages and demoting them to user messages is an error caused by incomplete catalog metadata.
- **Is the trigger supported usage?** Yes. Sending a chat completion request with `messages=[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]` is standard OpenAI API format supported by all LiteLLM chat routes.
- **Is a real boundary crossed?** Yes. Boundary crossing and security guardrail destruction. In AI agent systems, system instructions hold elevated instruction hierarchy over user content. When LiteLLM silently transforms `role: "system"` into `role: "user"`, the system guardrails become regular user turns, enabling subsequent user messages to override constraints and bypass safety boundaries.
- **What fix would a maintainer ship?** Set `"supports_system_messages": true` for `o3`, `o4-mini`, `o3-mini`, and their Azure variants in `litellm/model_prices_and_context_window.json`.
- **Classification**: `bug`

## What breaks

When an agent or client calls LiteLLM's chat completion endpoint targeting `o3`, `o4-mini`, or `o3-mini` with a system prompt:
1. `OpenAIOptionalParams.transform_request()` invokes `_transform_messages(messages, model)` in `litellm/llms/openai/chat/o_series_transformation.py`.
2. `supports_system_messages(model, "openai")` queries `model_prices_and_context_window.json`.
3. Because only `o1`, `o1-2024-12-17`, and `o1-pro` have `"supports_system_messages": true`, the query returns `False` for `o3`, `o4-mini`, and `o3-mini`.
4. The loop in lines 147-151 executes:
   ```python
   for i, message in enumerate(messages):
       if message["role"] == "system" and not _supports_system_messages:
           new_message = ChatCompletionUserMessage(
               content=message["content"], role="user"
           )
           messages[i] = new_message
   ```
5. Every `system` message is silently rewritten to `user`, so the outbound payload to OpenAI contains two consecutive user messages `[{"role": "user", "content": "system guardrail..."}, {"role": "user", "content": "user prompt..."}]`.
6. System guardrails, prompt rules, and agent identity lose their privileged standing in the model's attention mechanism, making the model susceptible to jailbreaks and prompt overriding.

## Wire evidence

- `bug-outbound-o3-system-demoted.json`: Captured outbound request for model `o3` where the original `system` message was demoted to `role: "user"`.
- `bug-outbound-o4mini-system-demoted.json`: Captured outbound request for model `o4-mini` where the original `system` message was demoted to `role: "user"`.
- `bug-outbound-o3mini-system-demoted.json`: Captured outbound request for model `o3-mini` where the original `system` message was demoted to `role: "user"`.
- `control-outbound-o1-system-preserved.json`: Control request for `o1` where the system message is correctly preserved with `role: "system"`.
- `control-outbound-fixed-system-preserved.json`: Control request simulating catalog metadata with `supports_system_messages: true`, preserving `role: "system"`.
- `results.json`: 5 of 5 runs producing identical results across all 5 conditions.

## Root cause (if found)

In `litellm/llms/openai/chat/o_series_transformation.py`, lines 143-151:
```python
_supports_system_messages: Final = supports_system_messages(model, "openai")
for i, message in enumerate(messages):
    if message["role"] == "system" and not _supports_system_messages:
        new_message = ChatCompletionUserMessage(
            content=message["content"], role="user"
        )
        messages[i] = new_message
```
Coupled with `litellm/model_prices_and_context_window.json` lacking `"supports_system_messages": true` entries for `o3`, `o4-mini`, `o3-mini`, and Azure variants.

## Test

- Invariant checker: `reasoning_model_system_prompt_preserved` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_o3_system_prompt_demoted_to_user_wire`
  * `litellm_o4mini_system_prompt_demoted_to_user_wire`
  * `litellm_o3mini_system_prompt_demoted_to_user_wire`
  * `litellm_o1_system_prompt_control_preserved_wire`
  * `litellm_o_series_fixed_system_prompt_control_wire`
  * `litellm_o_series_system_prompt_results_matrix_5_runs_reproduce_consistently`
