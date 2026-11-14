#!/usr/bin/env python3
"""Reproduction rig for Finding 135: LiteLLM CustomStreamWrapper non-async-iterable

fallback branch in __anext__ skips completion_start_time (corrupting TTFT)
and _set_final_chunk_usage (losing hidden-params usage for spend tracking).

Target: LiteLLM v1.109.0 (commit cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a / main 60fc121ce8a)
Upstream PR: https://github.com/BerriAI/litellm/pull/49572
"""

import asyncio
import datetime
import json
from pathlib import Path
from typing import Any, Dict, List

from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.litellm_core_utils.litellm_logging import Logging
from litellm.types.utils import ModelResponseStream, StreamingChoices, Delta, StreamingChoices


class SyncListIterator:
    """Synchronous iterator simulating non-aiohttp streams such as boto3 bedrock."""
    def __init__(self, chunks: List[Any]):
        self.chunks = iter(chunks)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.chunks)


class AsyncListIterator:
    """Asynchronous iterator simulating standard aiohttp / async streams."""
    def __init__(self, chunks: List[Any]):
        self.chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.chunks)
        except StopIteration:
            raise StopAsyncIteration


def make_test_chunks() -> List[ModelResponseStream]:
    return [
        ModelResponseStream(
            choices=[StreamingChoices(delta=Delta(content="Hello"), index=0, finish_reason=None)]
        ),
        ModelResponseStream(
            choices=[StreamingChoices(delta=Delta(content=" world"), index=0, finish_reason=None)]
        ),
        ModelResponseStream(
            choices=[StreamingChoices(delta=Delta(content=""), index=0, finish_reason="stop")],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
    ]


class FixedCustomStreamWrapper(CustomStreamWrapper):
    """CustomStreamWrapper patched with PR #49572 fix for non-async-iterable streams."""
    async def __anext__(self):
        if hasattr(self.completion_stream, "__anext__"):
            return await super().__anext__()

        while True:
            try:
                raw_chunk = next(self.completion_stream)
            except StopIteration:
                raise StopAsyncIteration

            processed_chunk = self.chunk_creator(raw_chunk)
            if processed_chunk is None:
                continue

            # PR #49572: record completion_start_time on first chunk
            if self.logging_obj.completion_start_time is None:
                self.logging_obj.update_completion_start_time(completion_start_time=datetime.datetime.now())

            if processed_chunk.choices:
                choice = processed_chunk.choices[0]
                if isinstance(choice, StreamingChoices):
                    self._accumulate_streamed_delta(choice.delta)
            self.rules.post_call_rules(input=self.response_uptil_now, model=self.model)

            if not self.sent_first_chunk and processed_chunk.choices:
                processed_chunk = self._add_mcp_list_tools_to_first_chunk(processed_chunk)
                self.sent_first_chunk = True

            if not self.send_stream_usage or getattr(processed_chunk, "usage", None) is None:
                self.chunks.append(processed_chunk)
            else:
                usage_free_chunk = self._record_and_strip_usage_chunk(processed_chunk)
                if usage_free_chunk is None:
                    continue
                processed_chunk = usage_free_chunk

            # PR #49572: record usage in hidden params when stream_options is None
            if self.sent_last_chunk is True and self.stream_options is None:
                self._set_final_chunk_usage(processed_chunk)

            # PR #49572: post-call streaming deployment hook and MCP metadata
            if self.sent_last_chunk is True:
                processed_chunk = await self._call_post_streaming_deployment_hook(processed_chunk)
                processed_chunk = self._add_mcp_metadata_to_final_chunk(processed_chunk)

            return processed_chunk


async def run_defect_sync_under_anext() -> Dict[str, Any]:
    log = Logging(
        model="bedrock/anthropic.claude-v2",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        call_type="acompletion",
        start_time=1.0,
        litellm_call_id="call-defect",
        function_id="fn-defect",
    )
    wrapper = CustomStreamWrapper(
        completion_stream=SyncListIterator(make_test_chunks()),
        model="bedrock/anthropic.claude-v2",
        custom_llm_provider="bedrock",
        logging_obj=log,
    )
    _c1 = await wrapper.__anext__()
    _c2 = await wrapper.__anext__()
    c3 = await wrapper.__anext__()

    hidden_usage = getattr(c3, "_hidden_params", {}).get("usage")
    return {
        "stream_mode": "async",
        "underlying_iterator": "sync_non_iterable",
        "has_completion_start_time": log.completion_start_time is not None,
        "completion_start_time": str(log.completion_start_time) if log.completion_start_time else None,
        "has_hidden_usage": hidden_usage is not None,
        "hidden_usage": hidden_usage.model_dump() if hidden_usage else None,
        "top_level_usage": getattr(c3, "usage", None).model_dump() if getattr(c3, "usage", None) else None,
        "finish_reason": c3.choices[0].finish_reason if c3.choices else None,
    }


async def run_control_async_iterable() -> Dict[str, Any]:
    log = Logging(
        model="bedrock/anthropic.claude-v2",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        call_type="acompletion",
        start_time=1.0,
        litellm_call_id="call-control-async",
        function_id="fn-control-async",
    )
    wrapper = CustomStreamWrapper(
        completion_stream=AsyncListIterator(make_test_chunks()),
        model="bedrock/anthropic.claude-v2",
        custom_llm_provider="bedrock",
        logging_obj=log,
    )
    _c1 = await wrapper.__anext__()
    _c2 = await wrapper.__anext__()
    c3 = await wrapper.__anext__()

    hidden_usage = getattr(c3, "_hidden_params", {}).get("usage")
    return {
        "stream_mode": "async",
        "underlying_iterator": "async_iterable",
        "has_completion_start_time": log.completion_start_time is not None,
        "completion_start_time": str(log.completion_start_time) if log.completion_start_time else None,
        "has_hidden_usage": hidden_usage is not None,
        "hidden_usage": hidden_usage.model_dump() if hidden_usage else None,
        "top_level_usage": getattr(c3, "usage", None).model_dump() if getattr(c3, "usage", None) else None,
        "finish_reason": c3.choices[0].finish_reason if c3.choices else None,
    }


async def run_control_fixed_sync_under_anext() -> Dict[str, Any]:
    log = Logging(
        model="bedrock/anthropic.claude-v2",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        call_type="acompletion",
        start_time=1.0,
        litellm_call_id="call-control-fixed",
        function_id="fn-control-fixed",
    )
    wrapper = FixedCustomStreamWrapper(
        completion_stream=SyncListIterator(make_test_chunks()),
        model="bedrock/anthropic.claude-v2",
        custom_llm_provider="bedrock",
        logging_obj=log,
    )
    _c1 = await wrapper.__anext__()
    _c2 = await wrapper.__anext__()
    c3 = await wrapper.__anext__()

    hidden_usage = getattr(c3, "_hidden_params", {}).get("usage")
    return {
        "stream_mode": "async",
        "underlying_iterator": "fixed_sync_non_iterable",
        "has_completion_start_time": log.completion_start_time is not None,
        "completion_start_time": str(log.completion_start_time) if log.completion_start_time else None,
        "has_hidden_usage": hidden_usage is not None,
        "hidden_usage": hidden_usage.model_dump() if hidden_usage else None,
        "top_level_usage": getattr(c3, "usage", None).model_dump() if getattr(c3, "usage", None) else None,
        "finish_reason": c3.choices[0].finish_reason if c3.choices else None,
    }


async def run_control_sync_include_usage() -> Dict[str, Any]:
    log = Logging(
        model="bedrock/anthropic.claude-v2",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        call_type="acompletion",
        start_time=1.0,
        litellm_call_id="call-control-include-usage",
        function_id="fn-control-include-usage",
    )
    wrapper = CustomStreamWrapper(
        completion_stream=SyncListIterator(make_test_chunks()),
        model="bedrock/anthropic.claude-v2",
        custom_llm_provider="bedrock",
        logging_obj=log,
        stream_options={"include_usage": True},
    )
    _c1 = await wrapper.__anext__()
    _c2 = await wrapper.__anext__()
    finish_chunk = await wrapper.__anext__()
    usage_chunk = await wrapper.__anext__()

    return {
        "stream_mode": "async",
        "underlying_iterator": "sync_with_include_usage",
        "finish_chunk_has_usage": getattr(finish_chunk, "usage", None) is not None,
        "usage_chunk_has_usage": getattr(usage_chunk, "usage", None) is not None,
        "usage_chunk_total_tokens": getattr(usage_chunk, "usage", None).total_tokens if getattr(usage_chunk, "usage", None) else None,
        "finish_reason": finish_chunk.choices[0].finish_reason if finish_chunk.choices else None,
    }


async def main():
    wire_dir = Path(__file__).resolve().parents[1] / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    iterations = 5
    run_records = []

    for i in range(iterations):
        defect = await run_defect_sync_under_anext()
        ctrl_async = await run_control_async_iterable()
        ctrl_fixed = await run_control_fixed_sync_under_anext()
        ctrl_usage = await run_control_sync_include_usage()

        assert defect["has_completion_start_time"] is False
        assert defect["has_hidden_usage"] is False

        assert ctrl_async["has_completion_start_time"] is True
        assert ctrl_async["has_hidden_usage"] is True

        assert ctrl_fixed["has_completion_start_time"] is True
        assert ctrl_fixed["has_hidden_usage"] is True

        assert ctrl_usage["usage_chunk_has_usage"] is True

        run_records.append({
            "run": i + 1,
            "defect_sync_under_anext_missing_ttft_and_usage": True,
            "control_async_iterable_preserved": True,
            "control_fixed_sync_preserved": True,
            "control_include_usage_delivered": True,
        })

    # Save wire capture samples
    defect_sample = await run_defect_sync_under_anext()
    ctrl_async_sample = await run_control_async_iterable()
    ctrl_fixed_sample = await run_control_fixed_sync_under_anext()
    ctrl_usage_sample = await run_control_sync_include_usage()

    (wire_dir / "bug-sync-iterator-under-anext-ttft-and-usage-lost.json").write_text(
        json.dumps(defect_sample, indent=2) + "\n"
    )
    (wire_dir / "control-async-iterator-under-anext-ttft-and-usage-preserved.json").write_text(
        json.dumps(ctrl_async_sample, indent=2) + "\n"
    )
    (wire_dir / "control-fixed-sync-iterator-under-anext-preserved.json").write_text(
        json.dumps(ctrl_fixed_sample, indent=2) + "\n"
    )
    (wire_dir / "control-sync-iterator-include-usage-preserved.json").write_text(
        json.dumps(ctrl_usage_sample, indent=2) + "\n"
    )

    results = {
        "iterations": iterations,
        "success_rate": f"{iterations}/{iterations}",
        "all_passed": True,
        "runs": run_records,
    }
    (wire_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (Path(__file__).resolve().parents[1] / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"Reproduced 135: {iterations}/{iterations} runs passed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
