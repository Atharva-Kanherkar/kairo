# 135, LiteLLM Async Non-Iterable Stream Branch Skips TTFT Timestamp, Hidden Final Chunk Usage, and Post-Streaming Hooks

- **Upstream**: Open PR [BerriAI/litellm#49572](https://github.com/BerriAI/litellm/pull/49572), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-02, macOS, Python 3.12, module `litellm.litellm_core_utils.streaming_handler`

## Bug or not

- **Is the expected behavior really the spec?** Yes. LiteLLM's asynchronous streaming contract (`CustomStreamWrapper.__anext__`) requires recording `completion_start_time` upon receiving the first chunk and extracting provider usage into `_hidden_params["usage"]` on the final chunk when `stream_options=None`. The main async-iterable branch adheres to this spec, while the non-async-iterable fallback branch silently skipped both.
- **Have maintainers already ruled on it?** Yes. In PR #49572 (`fix(streaming): align async non-iterable branch with main async path processing`), maintainers acknowledged that streams backed by non-async iterators (such as AWS Bedrock `boto3` event streams) were omitting TTFT calculation, hidden final-chunk usage, and post-call deployment hooks.
- **Is the trigger supported usage?** Yes. Calling `litellm.acompletion(model="bedrock/...", stream=True)` with AWS Bedrock or any backend whose iterator is synchronous is standard, documented usage.
- **Is a real boundary crossed?** Yes. Client-facing telemetry and proxy spend accounting boundary. When `completion_start_time` is skipped, Time-To-First-Token degrades to the full stream duration across all logging sinks (Datadog, OpenTelemetry, Prometheus). When `_set_final_chunk_usage` is skipped, `_hidden_params["usage"]` is missing, causing custom callbacks and spend tracking to record zero tokens.
- **What fix would a maintainer ship?** In `litellm/litellm_core_utils/streaming_handler.py`, update `completion_start_time` on the first chunk and invoke `_set_final_chunk_usage`, post-call streaming deployment hooks, and MCP metadata attachment on the final chunk in `CustomStreamWrapper.__anext__`.
- **Classification**: `bug`

## What breaks

When an application streams chat completions asynchronously via `acompletion` against AWS Bedrock or any provider backed by a synchronous iterator:
1. In `litellm/litellm_core_utils/streaming_handler.py`, `CustomStreamWrapper.__anext__` checks `hasattr(self.completion_stream, "__anext__")`.
2. Because the provider iterator is synchronous, execution enters the non-async-iterable fallback branch.
3. The fallback branch never calls `self.logging_obj.update_completion_start_time(...)`. In `litellm/litellm_core_utils/litellm_logging.py`, TTFT falls back to `completion_end_time`, falsely reporting a multi-second stream duration as the time to first token.
4. When `stream_options=None` (the default), `self._set_final_chunk_usage(...)` is never called. The final chunk arrives at spend logging hooks with `_hidden_params.get("usage") == None`.
5. Post-call streaming deployment hooks and MCP metadata attachments are skipped entirely.

## Wire evidence

- `bug-sync-iterator-under-anext-ttft-and-usage-lost.json`: Captured completion state showing `has_completion_start_time: false` and `hidden_usage: null`.
- `control-async-iterator-under-anext-ttft-and-usage-preserved.json`: Captured completion state under async iterator showing `has_completion_start_time: true` and populated `hidden_usage`.
- `control-fixed-sync-iterator-under-anext-preserved.json`: Captured completion state under PR #49572 fix showing preserved TTFT and hidden usage.
- `control-sync-iterator-include-usage-preserved.json`: Captured completion state showing separate usage chunk delivery under `include_usage: true`.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause (if found)

In `litellm/litellm_core_utils/streaming_handler.py`:
```python
if hasattr(self.completion_stream, "__anext__"):
    # Main branch sets completion_start_time and runs _set_final_chunk_usage
    ...
else:
    # Fallback branch omitted parity with the main branch:
    while True:
        ...
        if not self.send_stream_usage or getattr(processed_chunk, "usage", None) is None:
            self.chunks.append(processed_chunk)
            return processed_chunk
```
The fallback branch immediately returned early on every chunk without updating `completion_start_time` on the first chunk or executing `_set_final_chunk_usage` on the final chunk.

The fix introduces parity by:
1. Recording `completion_start_time` when `self.logging_obj.completion_start_time is None`.
2. Permitting execution to fall through to `if self.sent_last_chunk is True and self.stream_options is None: self._set_final_chunk_usage(processed_chunk)`.
3. Calling `_call_post_streaming_deployment_hook` and `_add_mcp_metadata_to_final_chunk`.

## Test

- Invariant checker: `async_stream_ttft_and_hidden_usage_preserved` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_sync_iterator_under_anext_ttft_and_usage_lost_reproduction`
  * `litellm_async_iterator_under_anext_ttft_and_usage_preserved_control`
  * `litellm_fixed_sync_iterator_under_anext_preserved_control`
  * `litellm_sync_iterator_include_usage_delivered_control`
  * `litellm_async_noniterable_matrix_5_runs_reproduce_consistently`
