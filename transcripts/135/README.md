# Transcript 135: LiteLLM Async Non-Iterable Stream Branch Skips TTFT Timestamp, Hidden Final Chunk Usage, and Post-Streaming Hooks

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's asynchronous stream wrapper (`CustomStreamWrapper.__anext__`) falls into a non-async-iterable fallback branch for synchronous-iterator-backed streams (such as AWS Bedrock `boto3` streams), skipping `completion_start_time` tracking (which falsifies Time-To-First-Token latency) and omitting `_set_final_chunk_usage` (which loses hidden-params token usage for spend tracking) (Upstream PR [BerriAI/litellm#49572](https://github.com/BerriAI/litellm/pull/49572)).

In `litellm/litellm_core_utils/streaming_handler.py`, `CustomStreamWrapper.__anext__` branches depending on whether `completion_stream` implements `__anext__`:
```python
if hasattr(self.completion_stream, "__anext__"):
    # Main async-iterable branch
    ...
    if self.logging_obj.completion_start_time is None:
        self.logging_obj.update_completion_start_time(completion_start_time=datetime.datetime.now())
    ...
    if self.sent_last_chunk is True and self.stream_options is None:
        self._set_final_chunk_usage(processed_chunk)
    ...
else:
    # Non-async-iterable fallback branch (boto3 bedrock, sync iterators wrapped in acompletion)
    while True:
        ...
        processed_chunk = self.chunk_creator(raw_chunk)
        ...
        if not self.send_stream_usage or getattr(processed_chunk, "usage", None) is None:
            self.chunks.append(processed_chunk)
            return processed_chunk
        usage_free_chunk = self._record_and_strip_usage_chunk(processed_chunk)
        if usage_free_chunk is None:
            continue
        return usage_free_chunk
```

In the fallback branch:
1. `self.logging_obj.completion_start_time` is never updated. In `litellm/litellm_core_utils/litellm_logging.py`, TTFT calculation falls back to stream completion end time, reporting the entire stream duration as time-to-first-token.
2. `self._set_final_chunk_usage(processed_chunk)` is never called when `stream_options=None`. Downstream proxy spend logs, post-call streaming deployment hooks, and custom logging callbacks that read `chunk._hidden_params.get("usage")` receive `None`, losing spend and token counts for Bedrock streams.
3. `async_post_call_streaming_deployment_hook` and MCP metadata attachment (`_add_mcp_list_tools_to_first_chunk`, `_add_mcp_metadata_to_final_chunk`) are completely bypassed.

The fix in PR #49572 brings parity to the fallback branch by updating `completion_start_time` on the first chunk and calling `_set_final_chunk_usage`, deployment hooks, and MCP metadata on the final chunk.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49572](https://github.com/BerriAI/litellm/pull/49572)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-02

## Differential Control Ladder

1. **Failing Case (Unpatched Non-Async-Iterable Stream under anext)**:
   - Call asynchronous `CustomStreamWrapper.__anext__` with a synchronous iterator (Bedrock/boto3 pattern) and `stream_options=None`.
   - Result: `has_completion_start_time` is `false` (TTFT corrupted) and `has_hidden_usage` is `false` (`_hidden_params["usage"]` is `None`).
2. **Control 1 (Async-Iterable Stream under anext)**:
   - Call asynchronous `CustomStreamWrapper.__anext__` with an asynchronous iterator.
   - Result: `has_completion_start_time` is `true` and `has_hidden_usage` is `true`.
3. **Control 2 (Fixed Non-Async-Iterable Stream under anext)**:
   - Call asynchronous `CustomStreamWrapper.__anext__` with PR #49572 patch applied.
   - Result: `has_completion_start_time` is `true` and `has_hidden_usage` is `true`.
4. **Control 3 (Sync Stream with include_usage)**:
   - Call with `stream_options={"include_usage": True}`.
   - Result: Usage is stripped from the finish chunk and delivered on a dedicated usage chunk (`total_tokens: 15`).

## Artifacts in `wire/`

- `bug-sync-iterator-under-anext-ttft-and-usage-lost.json`: Captured completion state showing missing TTFT timestamp and null hidden usage.
- `control-async-iterator-under-anext-ttft-and-usage-preserved.json`: Captured completion state under async iterator showing preserved TTFT and hidden usage.
- `control-fixed-sync-iterator-under-anext-preserved.json`: Captured completion state under PR #49572 fix showing preserved TTFT and hidden usage.
- `control-sync-iterator-include-usage-preserved.json`: Captured completion state showing include_usage delivering separate usage chunk.
- `results.json`: 5 of 5 runs producing identical results across all conditions.
