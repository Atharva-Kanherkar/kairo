# 119, LiteLLM native Anthropic MCP tool loop drops `is_error` flag from tool_result messages, misrepresenting execution failures as successful tool output

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). Reported in open issue
  [#49359](https://github.com/BerriAI/litellm/issues/49359) (filed 2026-10-31).
  Classified `duplicate-open`.
- **Tool under test**: LiteLLM proxy `v1.106.6` / `1.108.0-dev` at commit
  `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-31), with master key enabled.
- **Provider**: Deterministic local mock Anthropic server on `127.0.0.1:19996/v1/messages`
  implementing the Anthropic Messages tool use protocol.
- **Clients**: Standard Anthropic Messages API clients, `httpx` 0.28.1.
- **Reproduced**: 2026-11-01, macOS arm64, Python 3.12.13. 5 of 5 runs per case across
  2 differential conditions (10 requests total).
- **Label**: `bug`

## Claim

When LiteLLM executes Model Context Protocol (MCP) tool calls natively for Anthropic Claude models
on `POST /v1/messages`, tool execution failures (such as MCP errors with `isError=True` or caught
exceptions) drop the `is_error` flag. In `_build_tool_result_message`, the tool result content
block is constructed without `is_error=True`. When LiteLLM forwards the conversation history to
Anthropic on the next turn, the failure message is delivered as a regular successful tool result.
Because `is_error: true` is absent, Anthropic Claude interprets the error string as legitimate data
returned by the tool, failing to trigger tool retry or error correction logic and hallucinating based
on error text.

## What breaks

Developers and autonomous agent loops (such as Claude Code, Cursor, Windsurf, or OpenCode) connect
to Claude models with MCP tools hosted behind LiteLLM:

1. The client sends a prompt requiring MCP tool execution:
   `POST /v1/messages` with `tools: [{"type": "mcp", "server_url": "litellm_proxy/test_db", "require_approval": "never"}]`.
2. Anthropic responds with `stop_reason: "tool_use"`, requesting execution of `query_db`.
3. LiteLLM interceptor executes the tool call against the MCP server.
4. The tool execution fails (e.g. database timeout, connection refused, or an exception).
5. In `litellm/responses/mcp/litellm_proxy_mcp_handler.py:execute_tool_calls`, the failure is
   formatted into a plain string: `result: f"Error executing tool: {e}"`. The `MCPToolResult`
   TypedDict lacks an `is_error` field, so the error flag is lost.
6. In `litellm/llms/anthropic/pass_through/messages/mcp_handler.py:55` (`_build_tool_result_message`),
   LiteLLM constructs the Anthropic user tool result block:
   ```python
   AnthropicMessagesToolResultParam(
       type="tool_result",
       tool_use_id=str(result.get("tool_call_id") or ""),
       content=str(result.get("result") or ""),
   )
   ```
   It never sets `is_error=True`.
7. On Turn 2, LiteLLM calls Anthropic `/v1/messages` with the tool result.
8. The Anthropic upstream receives:
   ```json
   {
     "role": "user",
     "content": [
       {
         "type": "tool_result",
         "tool_use_id": "toolu_db_01",
         "content": "Error executing tool: 'query_db'"
       }
     ]
   }
   ```
9. Per Anthropic Messages API documentation, `is_error: true` is the explicit signal that tells Claude
   a tool execution failed. Without it, Claude interprets the text `"Error executing tool: 'query_db'"`
   as the actual data produced by the database query.
10. Claude does not attempt to retry the tool or adjust its arguments, and instead generates an
    answer incorporating the error message as ground truth.

## Bug or not

Before classifying this finding as a bug, the five usefulness questions from `AGENTS.md` are evaluated:

- **Is the expected behavior really the spec?** Yes. Anthropic Messages API specification explicitly
  defines `is_error: Optional[bool]` on `tool_result` blocks: "Set to true if the tool execution
  resulted in an error."
- **Have maintainers already ruled on it?** The issue was filed as [#49359](https://github.com/BerriAI/litellm/issues/49359)
  and acknowledged as an open bug.
- **Is the trigger supported usage?** Yes. Standard native Anthropic MCP tool execution via
  `/v1/messages` with `server_url: "litellm_proxy/..."`.
- **Is a real boundary crossed?** Yes. Semantic wire degradation: an execution error is stripped of
  its error classification and delivered as successful output to the model.
- **What fix would a maintainer ship?** Add `is_error` to `MCPToolResult`, populate `is_error: True`
  on exceptions and `CallToolResult.isError=True` in `execute_tool_calls`, and include
  `is_error: True` in `_build_tool_result_message`.

Record label: `bug`.

## Wire evidence

### Forwarded Turn 2 request to Anthropic from LiteLLM proxy (`anthropic-turn2-forwarded-request.json`)

LiteLLM forwarded the tool result without `is_error: true`:

```json
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_db_01",
      "content": "Error executing tool: 'query_db'"
    }
  ]
}
```

### Expected Turn 2 request to Anthropic (`expected.http`)

The gateway must set `is_error: true` when forwarding a failed tool execution:

```json
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_db_01",
      "content": "Error executing tool: 'query_db'",
      "is_error": true
    }
  ]
}
```

### Control direct request to Anthropic (`control-direct-anthropic-request.json`)

Direct client preserves `is_error: true`:

```json
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_control_01",
      "content": "Database connection error: Connection refused on port 5432",
      "is_error": true
    }
  ]
}
```

## Differential controls

Across 5 of 5 runs (see `transcripts/119/wire/results.json`):

1. **LiteLLM Turn 2 forwarded request**: 5 of 5 runs omit `is_error` on the tool result content block (the defect).
2. **Direct Anthropic control request**: 5 of 5 runs retain `is_error: true`.

## Root cause

1. In `litellm/responses/mcp/litellm_proxy_mcp_handler.py:execute_tool_calls`:
   `MCPToolResult` TypedDict defines only `tool_call_id`, `result`, and `name`. When an exception
   is caught or `isError=True` is returned by the MCP tool, `execute_tool_calls` appends a dictionary
   without `is_error`.
2. In `litellm/llms/anthropic/pass_through/messages/mcp_handler.py:55-61`:
   `_build_tool_result_message` instantiates `AnthropicMessagesToolResultParam` with only `type`,
   `tool_use_id`, and `content`, omitting `is_error`.

## Invariant and test

Invariant: In an Anthropic Messages API request containing an MCP tool execution failure,
the `tool_result` content block must carry `is_error: true`.

Harness checker: `anthropic_tool_result_preserves_is_error` in `crates/harness/src/checks.rs`.
Conformance tests: `litellm_anthropic_mcp_tool_result_drops_is_error_is_violation`,
`litellm_anthropic_mcp_control_preserves_is_error_is_conformant`, and
`litellm_anthropic_mcp_results_matrix_5_runs_reproduce_consistently` in `crates/harness/tests/conformance.rs`.
