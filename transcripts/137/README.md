# Transcript 137: LiteLLM GithubCopilotConfig Emits Unflattened List Content on System Messages

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's GitHub Copilot transformation handler (`GithubCopilotConfig._transform_messages`) forwards system messages containing list-form content blocks (`[{"type": "text", "text": "..."}]`) directly to the backend as unflattened lists of dictionaries instead of concatenating them into a single string (Upstream Issue [BerriAI/litellm#48568](https://github.com/BerriAI/litellm/issues/48568) and PR [BerriAI/litellm#48863](https://github.com/BerriAI/litellm/pull/48863)).

In `litellm/llms/github_copilot/chat/transformation.py`, `GithubCopilotConfig._transform_messages` converts message roles for compatibility:
```python
disable_conversion: Final = bool(litellm.disable_copilot_system_to_assistant)
transformed_messages: Final = []
for message in messages:
    if message.get("role") == "system":
        transformed_messages.append(
            cast(AllMessageValues, {**message, "role": "assistant"})
        )
    else:
        transformed_messages.append(message)
```

When callers submit system instructions using standard OpenAI or Anthropic content block formats:
```json
{
  "role": "system",
  "content": [
    {"type": "text", "text": "Always talk like a pirate."},
    {"type": "text", "text": " Ahoy!"}
  ]
}
```
The unpatched transformation preserves `content` as a list of dictionaries (`[{"type": "text", ...}]`).

Because GitHub Copilot's chat completions backend requires system instructions to be a flat string:
1. The backend silently ignores system prompts formatted as a list of blocks without returning an error.
2. The model answers user queries as if no system instructions were provided.
3. System personas, safety guardrails, response schemas, and context prompts are completely ignored by the model.

The fix in PR #48863 introduces `_flatten_text_content_blocks`, which concatenates text blocks into a single string and promotes any explicit `cache_control` markers to the message level while leaving multimodal blocks untouched.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream Issue / PR: [BerriAI/litellm#48568](https://github.com/BerriAI/litellm/issues/48568) / [BerriAI/litellm#48863](https://github.com/BerriAI/litellm/pull/48863)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-02

## Differential Control Ladder

1. **Failing Case (Unpatched GithubCopilotConfig)**:
   - Input: System message with list of text content blocks.
   - Result: Emits `content` as a list of dictionaries (`isinstance(content, list) == True`), which the Copilot backend ignores.
2. **Control 1 (Plain String System Content)**:
   - Input: System message with plain string content (`"Always talk like a pirate. Ahoy!"`).
   - Result: Emits `content` as a string (`isinstance(content, str) == True`).
3. **Control 2 (Fixed GithubCopilotConfig under PR #48863)**:
   - Input: System message with list of text content blocks.
   - Result: Flattens `content` to string `"Always talk like a pirate. Ahoy!"`.
4. **Control 3 (Cache Control Promotion)**:
   - Input: System message with text block carrying `cache_control`.
   - Result: Flattens `content` to string and promotes `cache_control` to top-level message.

## Artifacts in `wire/`

- `bug-github-copilot-system-list-content-unflattened.json`: Captured outbound message showing `content_is_list: true` and unflattened dictionary list.
- `control-plain-string-system-content.json`: Captured outbound message with plain string content.
- `control-fixed-system-list-content-flattened.json`: Captured outbound message under PR #48863 fix showing flattened string content.
- `control-cache-control-promoted.json`: Captured outbound message showing flattened text and promoted `cache_control`.
- `results.json`: 5 of 5 runs producing identical results across all conditions.
