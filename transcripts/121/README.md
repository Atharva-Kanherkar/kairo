# Transcript 121: LiteLLM ToolPolicyGuardrail Skips Trust Chain on Anthropic and Responses API and Allows Blocked Provider Tools

## Overview

This transcript records wire evidence reproducing the security vulnerability in LiteLLM's `ToolPolicyGuardrail` (Upstream issue [#49100](https://github.com/BerriAI/litellm/issues/49100)).

`ToolPolicyGuardrail` enforces tool authorization policies (`input_policy: "blocked" | "trusted" | "untrusted"` and `output_policy: "trusted" | "untrusted"`).
The implementation contains two security enforcement gaps when invoked through LiteLLM's unified translation handlers:

1. **Stage 2 Trust-Chain Enforcement Bypass on `/v1/messages` and `/v1/responses`**:
   - `_resolve_tool_names_from_messages()` and `apply_guardrail()` inspect only OpenAI Chat Completions formatting (`msg.get("role") == "tool"` and `msg.get("tool_calls")`).
   - On Anthropic `/v1/messages`, tool invocations use `content[].type == "tool_use"` and tool results use `role: "user"` with `content[].type == "tool_result"`.
   - On OpenAI `/v1/responses`, conversation history is in `request_data["input"]` (`function_call` and `function_call_output`), while `request_data.get("messages")` is `None`.
   - Consequently, `untrusted_sources` evaluates to `[]`, allowing sensitive tools with `input_policy="trusted"` (such as database execution or fund transfers) to execute immediately after untrusted tool outputs (such as prompt-injected web search results).
2. **Stage 1 Blocked Provider-Native Tool Bypass on Anthropic `/v1/messages`**:
   - `apply_guardrail(input_type="request")` only calls `extract_request_tool_names(route, request_data)` when `function_tool_names` is empty (`if route and not function_tool_names:`).
   - In `AnthropicMessagesHandler.process_input_messages()`, Anthropic provider-native tools (like `bash_20250124`) are excluded from `tools_to_check` (`inputs["tools"]`).
   - When a blocked provider-native tool (`bash_20250124`) is submitted alone, `function_tool_names` is empty, so `extract_request_tool_names` runs and blocks it with HTTP 400.
   - When `bash_20250124` is submitted alongside any custom tool (such as `ping`), `function_tool_names` is non-empty (`["ping"]`), so `extract_request_tool_names` is skipped entirely, allowing the blocked bash tool to pass through to the provider.

## Reproduction Environment

- Target: LiteLLM `v1.93.0` - `v1.109.0` (tested on commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main`)
- Target Upstream Issue: [BerriAI/litellm#49100](https://github.com/BerriAI/litellm/issues/49100)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Control 1 (OpenAI Chat Completions Stage 2 Trust Chain)**:
   - Call `/v1/chat/completions` where prior turn contains untrusted output from `web_search` (`output_policy="untrusted"`). Assistant attempts to invoke `database_exec` (`input_policy="trusted"`).
   - Result: Blocked with HTTP 400 Bad Request (`Violated tool policy`, `blocked_tools: ['database_exec']`, `untrusted_sources: ['web_search']`).
2. **Failing Case 1 (Anthropic Messages Stage 2 Trust Chain Bypass)**:
   - Identical conversation structure in Anthropic `/v1/messages` format (`tool_use` and `tool_result` content blocks).
   - Result: Guardrail skips Anthropic message blocks. Allowed through with HTTP 200 OK.
3. **Failing Case 2 (OpenAI Responses API Stage 2 Trust Chain Bypass)**:
   - Identical conversation structure in OpenAI Responses API `/v1/responses` format (`function_call` and `function_call_output` in `input`).
   - Result: Guardrail checks `request_data["messages"]` (which is `None`). Allowed through with HTTP 200 OK.
4. **Control 2 (Anthropic Messages Stage 1 Single Blocked Tool)**:
   - Call `/v1/messages` with `tools: [{"type": "bash_20250124", "name": "bash"}]` (`input_policy="blocked"`).
   - Result: `function_tool_names` is empty; `extract_request_tool_names` runs; blocked with HTTP 400 Bad Request.
5. **Failing Case 3 (Anthropic Messages Stage 1 Mixed Blocked Tool Bypass)**:
   - Call `/v1/messages` with `tools: [{"type": "bash_20250124", "name": "bash"}, {"name": "ping", ...}]`.
   - Result: `function_tool_names` is `["ping"]`; `extract_request_tool_names` is skipped; `bash` is allowed through with HTTP 200 OK.

## Artifacts in `wire/`

- `control-openai-chat-request.json`: OpenAI `/v1/chat/completions` request with untrusted tool output.
- `control-openai-chat-response.json`: LiteLLM HTTP 400 response blocking `database_exec`.
- `bug-anthropic-stage2-request.json`: Anthropic `/v1/messages` request with untrusted tool output.
- `bug-anthropic-stage2-response.json`: LiteLLM HTTP 200 response allowing `database_exec`.
- `bug-responses-stage2-request.json`: OpenAI Responses API `/v1/responses` request with untrusted function output.
- `bug-responses-stage2-response.json`: LiteLLM HTTP 200 response allowing `database_exec`.
- `control-anthropic-stage1-request.json`: Anthropic `/v1/messages` request containing only blocked `bash` tool.
- `control-anthropic-stage1-response.json`: LiteLLM HTTP 400 response blocking `bash`.
- `bug-anthropic-stage1-mixed-request.json`: Anthropic `/v1/messages` request containing blocked `bash` mixed with custom `ping` tool.
- `bug-anthropic-stage1-mixed-response.json`: LiteLLM HTTP 200 response allowing `bash` past Stage 1.
- `results.json`: 5 of 5 runs producing identical results.
