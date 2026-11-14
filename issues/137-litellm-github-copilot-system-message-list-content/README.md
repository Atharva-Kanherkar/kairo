# 137, LiteLLM GithubCopilotConfig Emits Unflattened List Content on System Messages, Causing Backend to Ignore System Prompts

- **Upstream**: Open Issue [BerriAI/litellm#48568](https://github.com/BerriAI/litellm/issues/48568), open PR [BerriAI/litellm#48863](https://github.com/BerriAI/litellm/pull/48863)
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-02, macOS, Python 3.12, module `litellm.llms.github_copilot.chat.transformation`

## Bug or not

- **Is the expected behavior really the spec?** Yes. OpenAI and Anthropic client SDKs support list-form content blocks for all message roles. When adapting requests to backends with strict string requirements (such as GitHub Copilot chat completions), the gateway is expected to normalize text-only content block lists into a single string.
- **Have maintainers already ruled on it?** Yes. Issue #48568 and PR #48863 confirm that the Copilot backend ignores system instructions formatted as block lists without returning an error, and prescribe flattening text-only content blocks to a string.
- **Is the trigger supported usage?** Yes. Calling `litellm.completion(model="github_copilot/...", messages=[{"role": "system", "content": [{"type": "text", "text": "..."}]}])` is documented, standard usage.
- **Is a real boundary crossed?** Yes. Inbound payload transformation and provider wire contract boundary. System prompts formatted as block lists are transmitted unflattened and ignored by GitHub Copilot, stripping all system instructions from the generation turn.
- **What fix would a maintainer ship?** In `litellm/llms/github_copilot/chat/transformation.py`, flatten text-only content block lists on system messages into a single string while promoting cache_control to the message level.
- **Classification**: `bug`

## What breaks

When an agent or application passes a system message formatted as a list of text blocks to a GitHub Copilot model:
1. In `litellm/llms/github_copilot/chat/transformation.py`, `GithubCopilotConfig._transform_messages` converts the role but leaves `content` as a list of dictionaries (`[{"type": "text", "text": "..."}]`).
2. The outbound request sent to GitHub Copilot's chat completions endpoint contains `content: [{"type": "text", "text": "..."}]`.
3. GitHub Copilot's backend expects system instructions as a plain string. It silently ignores list-form system prompts without raising an error.
4. The model responds as if no system instructions were provided, dropping developer directives, behavioral boundaries, and persona constraints.

## Wire evidence

- `bug-github-copilot-system-list-content-unflattened.json`: Outbound message showing `content_is_list: true` with unflattened dictionary list.
- `control-plain-string-system-content.json`: Outbound message showing plain string content preserved.
- `control-fixed-system-list-content-flattened.json`: Outbound message under PR #48863 fix showing flattened string content.
- `control-cache-control-promoted.json`: Outbound message showing text concatenation and promoted `cache_control`.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause (if found)

In `litellm/llms/github_copilot/chat/transformation.py`:
```python
transformed_messages: Final = []
for message in messages:
    if message.get("role") == "system":
        transformed_messages.append(
            cast(AllMessageValues, {**message, "role": "assistant"})
        )
    else:
        transformed_messages.append(message)
```
The method modified `message["role"]` but never inspected or flattened `message["content"]`.

The fix introduces `_flatten_text_content_blocks` to concatenate text blocks into a single string when `content` is a list of text blocks.

## Test

- Invariant checker: `github_copilot_system_message_flattened` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_github_copilot_system_list_content_unflattened_reproduction`
  * `litellm_github_copilot_plain_string_system_content_control`
  * `litellm_github_copilot_fixed_system_list_content_flattened_control`
  * `litellm_github_copilot_cache_control_promoted_control`
  * `litellm_github_copilot_system_matrix_5_runs_reproduce_consistently`
