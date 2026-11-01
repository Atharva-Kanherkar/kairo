# Transcript 134: LiteLLM Synchronous Stream Drops MCP Metadata on Final Chunk When stream_options.include_usage Is Enabled

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's synchronous stream wrapper (`CustomStreamWrapper.__next__`) omits MCP tool execution metadata (`provider_specific_fields.mcp_tool_calls`) from the final chunk when callers configure `stream_options={"include_usage": True}` (Upstream PR [BerriAI/litellm#49568](https://github.com/BerriAI/litellm/pull/49568)).

In `litellm/litellm_core_utils/streaming_handler.py`, synchronous `CustomStreamWrapper.__next__` attaches MCP metadata to the final chunk under an overly restrictive condition:
```python
if self.sent_last_chunk is True and self.stream_options is None:
    chunk = _add_mcp_metadata_to_final_chunk(
        chunk=chunk,
        logging_obj=self.logging_obj,
    )
```

When callers stream chat completions with `stream_options={"include_usage": True}`, `self.stream_options` is not `None`. As a result:
- `_add_mcp_metadata_to_final_chunk` is bypassed during synchronous iteration.
- The finish chunk (`finish_reason="stop"`) is emitted with `chunk.choices[0].delta.provider_specific_fields = None`.
- Downstream agent frameworks, observability pipelines, and UI clients that rely on `provider_specific_fields["mcp_tool_calls"]` fail to inspect or display tool execution details whenever token usage is requested.

By contrast, the asynchronous stream handler `__anext__` (lines 2276-2280) attaches MCP metadata unconditionally whenever `self.sent_last_chunk is True`:
```python
if self.sent_last_chunk is True:
    chunk = _add_mcp_metadata_to_final_chunk(
        chunk=chunk,
        logging_obj=self.logging_obj,
    )
```

The fix in PR #49568 aligns the synchronous `__next__` implementation with `__anext__` by removing the `and self.stream_options is None` restriction, ensuring MCP metadata is reliably attached to the final completion chunk before any separate usage-only chunk.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49568](https://github.com/BerriAI/litellm/pull/49568)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case (Unpatched Synchronous Stream with include_usage)**:
   - Call synchronous `CustomStreamWrapper.__next__` with `stream_options={"include_usage": True}` and MCP metadata present in logging hidden parameters.
   - Result: Final completion chunk has `delta.provider_specific_fields = None`. MCP tool call metadata is completely dropped.
2. **Control 1 (Asynchronous Stream with include_usage)**:
   - Call asynchronous `CustomStreamWrapper.__anext__` with `stream_options={"include_usage": True}`.
   - Result: Final completion chunk correctly includes `provider_specific_fields.mcp_tool_calls`.
3. **Control 2 (Fixed Synchronous Stream with include_usage)**:
   - Call synchronous `CustomStreamWrapper.__next__` with PR #49568 fix (un-gating `_add_mcp_metadata_to_final_chunk`).
   - Result: Final completion chunk correctly includes `provider_specific_fields.mcp_tool_calls`.
4. **Control 3 (Synchronous Stream without stream_options)**:
   - Call synchronous `CustomStreamWrapper.__next__` with `stream_options=None`.
   - Result: Final completion chunk correctly includes `provider_specific_fields.mcp_tool_calls`.

## Artifacts in `wire/`

- `bug-sync-stream-mcp-metadata-dropped.json`: Captured finish chunk from synchronous stream showing `delta.provider_specific_fields = None`.
- `control-async-stream-mcp-metadata-preserved.json`: Captured finish chunk from async stream showing preserved `provider_specific_fields`.
- `control-fixed-sync-stream-mcp-metadata-preserved.json`: Captured finish chunk from patched sync stream showing preserved `provider_specific_fields`.
- `control-sync-stream-no-options-preserved.json`: Captured finish chunk from sync stream without `stream_options` showing preserved `provider_specific_fields`.
- `results.json`: 5 of 5 runs producing identical results across all conditions.
