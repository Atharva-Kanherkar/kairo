# 121, LiteLLM ToolPolicyGuardrail Skips Trust Chain on Anthropic and Responses API and Allows Blocked Provider Tools

- **Upstream**: [BerriAI/litellm#49100](https://github.com/BerriAI/litellm/issues/49100), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.93.0` - `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, routes `/v1/messages`, `/v1/responses`, and `/v1/chat/completions`

## Bug or not

- **Is the expected behavior really the spec?** Yes. LiteLLM's `ToolPolicyGuardrail` specification explicitly documents:
  `"trusted" - only allow if conversation contains no untrusted tool output`, and `"blocked" - raise HTTPException, preventing the tool call`.
- **Have maintainers already ruled on it?** No. Upstream issue #49100 is open with no ruling or counter-classification.
- **Is the trigger supported usage?** Yes. Using `ToolPolicyGuardrail` with Anthropic Claude on `/v1/messages` and OpenAI Models on `/v1/responses` are standard supported entry points for LiteLLM's multi-provider gateway.
- **Is a real boundary crossed?** Yes. Security trust boundary: callers and untrusted tool outputs bypass prompt injection protection on `/v1/messages` and `/v1/responses`, and callers access blocked execution sandboxes (`bash_20250124`) by mixing with custom tools.
- **What fix would a maintainer ship?** Update `ToolPolicyGuardrail` to parse tool calls and tool results from Anthropic message content blocks and Responses API input items, and ensure Stage 1 request validation checks route-extracted tool names unconditionally rather than only when `function_tool_names` is empty.
- **Classification**: `bug`

## What breaks

In LiteLLM proxy deployments configured with `ToolPolicyGuardrail`, security teams enforce authorization controls over model tool usage:
1. `input_policy: "trusted"` restricts high-privilege tools (such as database query execution, financial transactions, or shell execution) so that they can never be executed after untrusted tools (such as web search, document retrieval, or user scraping) have returned data into the conversation context. This prevents prompt injection attacks where an untrusted tool's payload hijacks the model into invoking sensitive tools.
2. `input_policy: "blocked"` prohibits specific tools entirely (such as Anthropic's provider-native `bash_20250124` sandbox).

Two critical bypass vulnerabilities break these guarantees on non-OpenAI chat endpoints:
1. **Stage 2 Trust Chain Bypass**: When requests are made via Anthropic `/v1/messages` or OpenAI Responses API `/v1/responses`, `ToolPolicyGuardrail` inspects only OpenAI chat completion message formats (`msg.get("role") == "tool"` and `msg.get("tool_calls")`). Because Anthropic formats tool results inside `role: "user"` content blocks (`type: "tool_result"`) and Responses API formats them inside `input` arrays (`type: "function_call_output"`), `ToolPolicyGuardrail` fails to detect any untrusted outputs. It allows sensitive `trusted` tools to be executed following untrusted outputs without raising HTTP 400.
2. **Stage 1 Blocked Tool Bypass**: When an Anthropic `/v1/messages` request includes a blocked provider-native tool (`bash_20250124`) alongside any custom tool (such as `ping`), `inputs["tools"]` contains the custom tool. Because `function_tool_names` is non-empty, `ToolPolicyGuardrail` skips calling `extract_request_tool_names(route, request_data)`. As a result, the blocked `bash` tool is never inspected and is forwarded directly to Anthropic.

## Wire evidence

- `control-openai-chat-request.json`: OpenAI `/v1/chat/completions` request where `web_search` output precedes `database_exec` tool call.
- `control-openai-chat-response.json`: LiteLLM correctly blocks with HTTP 400 (`Violated tool policy`, `blocked_tools: ['database_exec']`, `untrusted_sources: ['web_search']`).
- `bug-anthropic-stage2-request.json`: Identical scenario via Anthropic `/v1/messages`.
- `bug-anthropic-stage2-response.json`: LiteLLM allows the request with HTTP 200 OK, bypassing the trust chain.
- `bug-responses-stage2-request.json`: Identical scenario via OpenAI `/v1/responses`.
- `bug-responses-stage2-response.json`: LiteLLM allows the request with HTTP 200 OK, bypassing the trust chain.
- `control-anthropic-stage1-request.json`: Anthropic `/v1/messages` request with only blocked `bash` tool. LiteLLM blocks with HTTP 400.
- `bug-anthropic-stage1-mixed-request.json`: Anthropic `/v1/messages` request with blocked `bash` mixed with custom `ping` tool. LiteLLM allows with HTTP 200 OK.

## Root cause (if found)

In `litellm/proxy/guardrails/guardrail_hooks/tool_policy/tool_policy_guardrail.py`:
1. Lines 150-154:
   ```python
   tool_names = (
       extract_request_tool_names(route, request_data)
       if route and not function_tool_names
       else [*function_tool_names, *replayed_tool_names]
   )
   ```
   When `function_tool_names` is non-empty, `extract_request_tool_names` is never invoked, omitting provider-native tools from Stage 1 policy enforcement.
2. Lines 92-112:
   ```python
   def _resolve_tool_names_from_messages(messages: list[dict]) -> dict[str, str]:
       mapping: Final[dict[str, str]] = {}
       for msg in messages:
           if msg.get("role") != "assistant":
               continue
           tool_calls = msg.get("tool_calls") or []
           for tc in tool_calls:
               ...
   ```
   Inspects only OpenAI `msg.get("tool_calls")` on `role: "assistant"`, missing Anthropic `content[].type == "tool_use"` and Responses API `input[].type == "function_call"`.
3. Lines 211-220:
   ```python
   for msg in messages:
       if msg.get("role") != "tool":
           continue
       tool_call_id = msg.get("tool_call_id")
   ```
   Inspects only OpenAI `role == "tool"` messages, missing Anthropic `role == "user"` blocks with `type == "tool_result"` and Responses API `type == "function_call_output"`.

## Test

- Invariant checker: `tool_policy_guardrail_enforces_trust_chain_and_blocked_tools` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_tool_policy_guardrail_anthropic_stage2_trust_chain_bypass_is_violation`
  * `litellm_tool_policy_guardrail_responses_api_stage2_trust_chain_bypass_is_violation`
  * `litellm_tool_policy_guardrail_anthropic_stage1_mixed_blocked_tool_bypass_is_violation`
  * `litellm_tool_policy_guardrail_openai_chat_control_is_conformant`
  * `litellm_tool_policy_guardrail_results_matrix_5_runs_reproduce_consistently`
