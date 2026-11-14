# 123, LiteLLM /v1/messages Drops Encrypted Reasoning on chatgpt/ Provider Across Tool Calls

- **Upstream**: [BerriAI/litellm#49046](https://github.com/BerriAI/litellm/issues/49046), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, route `/v1/messages`

## Bug or not

- **Is the expected behavior really the spec?** Yes. For reasoning models behind the Responses API bridge, multi-turn tool-use loops rely on encrypted reasoning tokens (`reasoning.encrypted_content`) being returned to the client and replayed on subsequent turns. LiteLLM already defines `litellm_encrypted_reasoning:<token>` as the canonical wire envelope and implemented `responses_reasoning_items_from_thinking_blocks` specifically to reconstruct Responses API reasoning items from thinking blocks.
- **Have maintainers already ruled on it?** Yes. BerriAI maintainers implemented PR #40451 for the direct `responses_adapters` path to preserve and unwrap encrypted reasoning blocks on `/v1/messages`. However, the chat completions bridge used by `chatgpt/` (ChatGPT Codex backend) and `use_chat_completions_url_for_anthropic_messages: true` was omitted from this support.
- **Is the trigger supported usage?** Yes. Pointing an Anthropic Messages client (e.g. Claude Code or OpenCode) at LiteLLM proxy configured with `chatgpt/` models (e.g. `chatgpt/gpt-6.1-sol`) with extended thinking enabled is documented and supported.
- **Is a real boundary crossed?** Yes. Reasoning context continuity across multi-turn agent tool executions. Because reasoning tokens are dropped outbound and stripped inbound, the backend starts reasoning from scratch on every single tool call turn.
- **What fix would a maintainer ship?** Map `reasoning_items` to signed thinking blocks in `_translate_openai_content_to_anthropic`, and avoid stripping `litellm_encrypted_reasoning:` blocks in `translate_anthropic_messages_to_openai` when targeting providers that bridge into the Responses API.
- **Classification**: `bug`

## What breaks

When an Anthropic Messages client (such as Claude Code) interacts with LiteLLM proxy backed by `chatgpt/` or an OpenAI model bridged via chat completions:
1. **Outbound response path**: The Responses bridge places the backend reasoning item in `reasoning_items` on the chat completion message (`{'type': 'reasoning', 'id': 'rs_...', 'encrypted_content': '...'}`). However, `LiteLLMAnthropicMessagesAdapter._translate_openai_content_to_anthropic` only inspects `choice.message.thinking_blocks` and `choice.message.reasoning_content`. It completely ignores `choice.message.reasoning_items`. As a result, no `thinking` or `redacted_thinking` block is included in the Anthropic response to the client; only `tool_use` is returned.
2. **Inbound request path**: Even if a client presents an assistant message carrying the canonical encrypted reasoning envelope (`signature: "litellm_encrypted_reasoning:<enc>"` or `data: "litellm_encrypted_reasoning:<enc>"`), `translate_anthropic_messages_to_openai` unconditionally calls `strip_encrypted_reasoning_blocks_from_anthropic_messages(messages)`. This wipes out the thinking block before the request reaches the Responses transformation bridge (`_convert_chat_completion_messages_to_responses_input`), causing `_reasoning_input_items` to evaluate to empty `[]`.
3. **Consequence**:
   - Every tool call turn loses reasoning state and restarts reasoning from scratch.
   - Prompt caching across tool turns is broken or degraded.
   - Multi-turn agent runs suffer approximately 30% to 35% higher wall-clock latency and more than double the reasoning token consumption.

## Wire evidence

- `control-outbound-reasoning-preserved.json`: Control direct adapter translates backend reasoning item into Anthropic `redacted_thinking` block containing `litellm_encrypted_reasoning:gAAAAABn...`.
- `bug-outbound-reasoning-items-dropped.json`: Defect path drops `reasoning_items`, returning only `tool_use` in content blocks (0 thinking blocks).
- `control-inbound-reasoning-replayed.json`: Control direct adapter transforms replayed thinking block into backend Responses API `input` item of `type: reasoning` with `encrypted_content`.
- `bug-inbound-reasoning-stripped.json`: Defect path strips replayed thinking block, forwarding only `message`, `function_call`, and `function_call_output` with 0 reasoning items.

## Root cause (if found)

1. Outbound: In `litellm/llms/anthropic/pass_through/adapters/transformation.py`, lines 1378-1405:
   `_translate_openai_content_to_anthropic` checks `choice.message.thinking_blocks` and `choice.message.reasoning_content`, but lacks handling for `choice.message.reasoning_items` containing `encrypted_content`.
2. Inbound: In `litellm/llms/anthropic/pass_through/adapters/transformation.py`, line 477:
   ```python
   replayable_messages: Final = strip_encrypted_reasoning_blocks_from_anthropic_messages(messages)
   ```
   `translate_anthropic_messages_to_openai` assumes that chat completions never targets the Responses API, unconditionally stripping all `litellm_encrypted_reasoning:` blocks.

## Test

- Invariant checker: `anthropic_messages_responses_bridge_preserves_encrypted_reasoning` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_chatgpt_messages_outbound_reasoning_dropped_wire`
  * `litellm_chatgpt_messages_outbound_reasoning_control_preserved_wire`
  * `litellm_chatgpt_messages_inbound_reasoning_stripped_wire`
  * `litellm_chatgpt_messages_inbound_reasoning_control_replayed_wire`
  * `litellm_chatgpt_messages_results_matrix_5_runs_reproduce_consistently`
