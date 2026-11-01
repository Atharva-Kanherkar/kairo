# Transcript 126: LiteLLM o-Series System Prompt Demoted to User on o3, o4-mini, and o3-mini

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM silently demotes system prompts into user messages for OpenAI reasoning models `o3`, `o4-mini`, and `o3-mini` (Upstream issue [#48731](https://github.com/BerriAI/litellm/issues/48731), open PR [#48732](https://github.com/BerriAI/litellm/pull/48732)).

When sending a chat completion payload with `messages=[{"role": "system", ...}, {"role": "user", ...}]`, LiteLLM's `_transform_messages()` checks `supports_system_messages(model, "openai")`. In `model_prices_and_context_window.json`, only `o1` variants have `"supports_system_messages": true`. `o3`, `o4-mini`, and `o3-mini` lack this key, returning `False`. LiteLLM then rewrites every `system` message to `user`:

```python
new_message = ChatCompletionUserMessage(content=message["content"], role="user")
messages[i] = new_message
```

This destroys instruction hierarchy, converting privileged system-level safety guardrails into ordinary user inputs that downstream conversation turns can bypass.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream Issue: [BerriAI/litellm#48731](https://github.com/BerriAI/litellm/issues/48731)
- Upstream PR: [BerriAI/litellm#48732](https://github.com/BerriAI/litellm/pull/48732)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case (Model o3)**:
   - Call LiteLLM with `model="o3"` and a system message.
   - Result: System message is demoted to `role: "user"`. Outbound message roles: `['user', 'user']`.
2. **Failing Case (Model o4-mini)**:
   - Call LiteLLM with `model="o4-mini"` and a system message.
   - Result: System message is demoted to `role: "user"`. Outbound message roles: `['user', 'user']`.
3. **Failing Case (Model o3-mini)**:
   - Call LiteLLM with `model="o3-mini"` and a system message.
   - Result: System message is demoted to `role: "user"`. Outbound message roles: `['user', 'user']`.
4. **Control 1 (Model o1)**:
   - Call LiteLLM with `model="o1"` and a system message.
   - Result: System message is preserved with `role: "system"`. Outbound message roles: `['system', 'user']`.
5. **Control 2 (Fixed catalog entry)**:
   - Call transformation with `supports_system_messages: true`.
   - Result: System message is preserved with `role: "system"`. Outbound message roles: `['system', 'user']`.

## Artifacts in `wire/`

- `bug-outbound-o3-system-demoted.json`: Outbound request showing `o3` message transformed to role `user`.
- `bug-outbound-o4mini-system-demoted.json`: Outbound request showing `o4-mini` message transformed to role `user`.
- `bug-outbound-o3mini-system-demoted.json`: Outbound request showing `o3-mini` message transformed to role `user`.
- `control-outbound-o1-system-preserved.json`: Outbound request showing `o1` keeping role `system`.
- `control-outbound-fixed-system-preserved.json`: Outbound request showing `supports_system_messages: true` keeping role `system`.
- `results.json`: 5 of 5 runs producing identical results across all 5 conditions.
