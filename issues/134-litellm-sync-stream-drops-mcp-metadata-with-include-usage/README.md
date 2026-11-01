# 134, LiteLLM Synchronous Stream Drops MCP Metadata on Final Chunk When stream_options.include_usage Is Enabled

- **Upstream**: Open PR [BerriAI/litellm#49568](https://github.com/BerriAI/litellm/pull/49568), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.litellm_core_utils.streaming_handler`

## Bug or not

- **Is the expected behavior really the spec?** Yes. LiteLLM documents MCP support and returns MCP tool execution details (`mcp_tool_calls`, `mcp_call_results`) on streaming completions via `delta.provider_specific_fields`. Enabling token usage tracking (`stream_options={"include_usage": True}`) is a standard OpenAI-compatible stream option that should not drop MCP execution payloads.
- **Have maintainers already ruled on it?** Yes. In PR #49568 (`fix(streaming): surface MCP metadata on sync final chunk when include_usage`), maintainers acknowledged that synchronous streaming incorrectly gated MCP metadata attachment on `self.stream_options is None`, causing silent metadata drops whenever callers requested usage reporting.
- **Is the trigger supported usage?** Yes. Streaming chat completions synchronously using the standard LiteLLM client while requesting token usage via `stream_options={"include_usage": True}` is documented, standard usage.
- **Is a real boundary crossed?** Yes. Client-facing streaming wire contract boundary. When `include_usage` is requested on synchronous streams, LiteLLM strips `provider_specific_fields` containing MCP tool calls from the completion stream, preventing clients from receiving tool execution metadata.
- **What fix would a maintainer ship?** In `litellm/litellm_core_utils/streaming_handler.py`, remove the `and self.stream_options is None` check from `CustomStreamWrapper.__next__` so `_add_mcp_metadata_to_final_chunk` runs whenever `self.sent_last_chunk is True`.
- **Classification**: `bug`

## What breaks

When an agent framework or client application invokes synchronous streaming chat completions with MCP tools and token usage tracking:
1. The client configures `stream=True` and `stream_options={"include_usage": True}` to track token consumption.
2. In `litellm/litellm_core_utils/streaming_handler.py`, `CustomStreamWrapper.__next__` checks:
   ```python
   if self.sent_last_chunk is True and self.stream_options is None:
       chunk = _add_mcp_metadata_to_final_chunk(
           chunk=chunk,
           logging_obj=self.logging_obj,
       )
   ```
3. Because `self.stream_options` is not `None`, `_add_mcp_metadata_to_final_chunk` is bypassed.
4. The final finish chunk (`finish_reason="stop"`) arrives at the client with `chunk.choices[0].delta.provider_specific_fields = None`.
5. Downstream agent loops that inspect MCP tool execution results or record tool provenance lose all MCP execution context, while the asynchronous code path (`__anext__`) returns the metadata properly.

## Wire evidence

- `bug-sync-stream-mcp-metadata-dropped.json`: Captured synchronous finish chunk showing `provider_specific_fields: null` despite MCP tool calls executed.
- `control-async-stream-mcp-metadata-preserved.json`: Captured asynchronous finish chunk showing preserved `provider_specific_fields.mcp_tool_calls`.
- `control-fixed-sync-stream-mcp-metadata-preserved.json`: Captured synchronous finish chunk under PR #49568 fix showing preserved `provider_specific_fields`.
- `control-sync-stream-no-options-preserved.json`: Captured synchronous finish chunk without `stream_options` showing preserved `provider_specific_fields`.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause (if found)

In `litellm/litellm_core_utils/streaming_handler.py`:
```python
if self.sent_last_chunk is True and self.stream_options is None:
    chunk = _add_mcp_metadata_to_final_chunk(
        chunk=chunk,
        logging_obj=self.logging_obj,
    )
```

The author of the synchronous stream handler intended to avoid attaching choices metadata to the trailing usage chunk emitted when `stream_options={"include_usage": True}`. However, checking `self.stream_options is None` completely prevented `_add_mcp_metadata_to_final_chunk` from executing on the preceding finish chunk (where `self.sent_last_chunk is True` and `choices` are present).

In contrast, the async handler in `__anext__` (lines 2276-2280) correctly executes:
```python
if self.sent_last_chunk is True:
    chunk = _add_mcp_metadata_to_final_chunk(
        chunk=chunk,
        logging_obj=self.logging_obj,
    )
```

The fix unifies the synchronous handler with the asynchronous handler by removing `and self.stream_options is None`.

## Test

- Invariant checker: `sync_stream_mcp_metadata_preserved` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_sync_stream_drops_mcp_metadata_with_include_usage_reproduction`
  * `litellm_async_stream_preserves_mcp_metadata_with_include_usage_control`
  * `litellm_fixed_sync_stream_preserves_mcp_metadata_with_include_usage_control`
  * `litellm_sync_stream_no_options_preserves_mcp_metadata_control`
  * `litellm_sync_stream_mcp_metadata_matrix_5_runs_reproduce_consistently`
