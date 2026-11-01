# Transcript 123: LiteLLM /v1/messages Drops Encrypted Reasoning on chatgpt/ Provider Across Tool Calls

## Overview

This transcript records wire evidence reproducing the defect in LiteLLM Proxy's `/v1/messages` endpoint when routing through the chat completions bridge for `chatgpt/` (ChatGPT Codex backend) and Responses API providers (Upstream issue [#49046](https://github.com/BerriAI/litellm/issues/49046)).

When an Anthropic Messages client (e.g. Claude Code or OpenCode) interacts with LiteLLM proxy backed by `chatgpt/`:
1. On outbound turns, the Responses bridge returns reasoning items with encrypted content (`reasoning_items`). However, `_translate_openai_content_to_anthropic` only inspects `thinking_blocks` and `reasoning_content`, ignoring `reasoning_items`. Thus, the client never receives a thinking or redacted_thinking block.
2. On inbound turns, when the client sends an assistant turn containing a signed thinking block (`signature: "litellm_encrypted_reasoning:<token>"`), `translate_anthropic_messages_to_openai` unconditionally strips the block via `strip_encrypted_reasoning_blocks_from_anthropic_messages`.
3. Consequently, the backend never receives the replayed reasoning item in `input`, and every tool call turn restarts reasoning from scratch, destroying prompt caching and inflating execution time and cost.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main`)
- Target Upstream Issue: [BerriAI/litellm#49046](https://github.com/BerriAI/litellm/issues/49046)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Control 1 (Outbound reasoning preservation via direct Responses adapter)**:
   - Backend returns response containing reasoning item (`type: reasoning`, `encrypted_content: "gAAAAABn..."`) and function call.
   - Result: `LiteLLMAnthropicToResponsesAPIAdapter.translate_response` translates the item to a `redacted_thinking` block containing `litellm_encrypted_reasoning:gAAAAABn...`.
2. **Failing Case 1 (Outbound reasoning dropped via chat completions bridge)**:
   - Same backend response processed through `LiteLLMResponsesTransformationHandler.transform_response` and `LiteLLMAnthropicMessagesAdapter.translate_openai_response_to_anthropic`.
   - Result: `reasoning_items` is ignored. Anthropic response content has 0 thinking blocks, containing only `tool_use`.
3. **Control 2 (Inbound reasoning replay via direct Responses adapter)**:
   - Client sends assistant turn containing thinking block with `signature: "litellm_encrypted_reasoning:gAAAAABn..."`.
   - Result: `LiteLLMAnthropicToResponsesAPIAdapter.translate_messages_to_responses_input` extracts the thinking block into a Responses API `input` item of `type: reasoning` with matching `encrypted_content`.
4. **Failing Case 2 (Inbound reasoning stripped via chat completions bridge)**:
   - Same client message processed through `LiteLLMAnthropicMessagesAdapter.translate_anthropic_messages_to_openai` and `LiteLLMResponsesTransformationHandler._convert_chat_completion_messages_to_responses_input`.
   - Result: `strip_encrypted_reasoning_blocks_from_anthropic_messages` strips the thinking block. Forwarded `input_items` contains only `message`, `function_call`, and `function_call_output`, with 0 reasoning items.

## Artifacts in `wire/`

- `control-outbound-reasoning-preserved.json`: Anthropic response with preserved `redacted_thinking` block.
- `bug-outbound-reasoning-items-dropped.json`: Anthropic response with dropped thinking blocks (only `tool_use`).
- `control-inbound-reasoning-replayed.json`: Responses API request with replayed `reasoning` item.
- `bug-inbound-reasoning-stripped.json`: Responses API request with stripped reasoning item.
- `results.json`: 5 of 5 runs producing identical results across all 4 conditions.
