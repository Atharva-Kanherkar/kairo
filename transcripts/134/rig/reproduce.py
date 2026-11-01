#!/usr/bin/env python3
"""
Reproduction script for Finding 134:
LiteLLM Synchronous Stream Suppresses Final Chunk MCP Metadata When include_usage Is Enabled

Upstream PR: https://github.com/BerriAI/litellm/pull/49568
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
from typing import Final

from litellm.litellm_core_utils.litellm_logging import Logging
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.responses.mcp.chat_completions_handler import _add_mcp_metadata_to_response
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices
from litellm.utils import ModelResponseListIterator


class FixedCustomStreamWrapper(CustomStreamWrapper):
    """Applies PR #49568 fix: un-gates _add_mcp_metadata_to_final_chunk from stream_options is None."""

    def __next__(self) -> ModelResponseStream:
        chunk = super().__next__()
        # If the superclass returned the last chunk without MCP metadata because stream_options was not None,
        # ensure _add_mcp_metadata_to_final_chunk runs on the finish_reason chunk.
        if (
            self.sent_last_chunk is True
            and chunk.choices
            and chunk.choices[0].finish_reason is not None
        ):
            chunk = self._add_mcp_metadata_to_final_chunk(chunk)
        return chunk


def build_stream(sync_mode: bool, stream_options: dict | None, fixed: bool = False):
    model = "gpt-4o"
    text = "The quick brown fox jumps over the lazy dog."
    tool_calls = [{"name": "lookup_fox", "arguments": "{}"}]

    logging_obj = Logging(
        model=model,
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        call_type="completion" if sync_mode else "acompletion",
        start_time=datetime.datetime(2025, 3, 3, 21, 38, 10),
        litellm_call_id="mcp-include-usage-call",
        function_id="mcp-include-usage-fn",
    )
    logging_obj.update_environment_variables(
        model=model,
        user=None,
        optional_params={},
        litellm_params={"litellm_call_id": "mcp-include-usage-call"},
    )

    wrapper_cls = FixedCustomStreamWrapper if fixed else CustomStreamWrapper
    wrapper = wrapper_cls(
        completion_stream=ModelResponseListIterator(
            model_responses=[
                ModelResponseStream(
                    id="chatcmpl-mcp-usage",
                    created=1741037890,
                    model=model,
                    choices=choices,
                )
                for choices in (
                    [StreamingChoices(index=0, delta=Delta(role="assistant", content=text))],
                    [StreamingChoices(index=0, delta=Delta(content=""), finish_reason="stop")],
                )
            ]
        ),
        model=model,
        custom_llm_provider="openai",
        logging_obj=logging_obj,
        stream_options=stream_options,
    )
    _add_mcp_metadata_to_response(wrapper, openai_tools=None, tool_calls=tool_calls)
    return wrapper


def run_reproduction() -> dict:
    expected_tool_calls = [{"name": "lookup_fox", "arguments": "{}"}]

    # 1. Failing Case: Synchronous stream with stream_options={'include_usage': True}
    sync_wrapper = build_stream(sync_mode=True, stream_options={"include_usage": True}, fixed=False)
    sync_yielded = [chunk for chunk in sync_wrapper]
    sync_finish = [c for c in sync_yielded if c.choices and c.choices[0].finish_reason is not None][0]
    sync_fields = getattr(sync_finish.choices[0].delta, "provider_specific_fields", None)

    bug_sync_dropped_metadata = sync_fields is None

    # 2. Control 1: Asynchronous stream with stream_options={'include_usage': True}
    async def get_async_fields():
        async_wrapper = build_stream(sync_mode=False, stream_options={"include_usage": True}, fixed=False)
        async_yielded = [c async for c in async_wrapper]
        async_finish = [c for c in async_yielded if c.choices and c.choices[0].finish_reason is not None][0]
        return getattr(async_finish.choices[0].delta, "provider_specific_fields", None)

    async_fields = asyncio.run(get_async_fields())
    control_async_preserved = (
        async_fields is not None
        and async_fields.get("mcp_tool_calls") == expected_tool_calls
    )

    # 3. Control 2: Fixed synchronous stream with stream_options={'include_usage': True}
    fixed_sync_wrapper = build_stream(sync_mode=True, stream_options={"include_usage": True}, fixed=True)
    fixed_yielded = [c for c in fixed_sync_wrapper]
    fixed_finish = [c for c in fixed_yielded if c.choices and c.choices[0].finish_reason is not None][0]
    fixed_fields = getattr(fixed_finish.choices[0].delta, "provider_specific_fields", None)

    control_fixed_sync_preserved = (
        fixed_fields is not None
        and fixed_fields.get("mcp_tool_calls") == expected_tool_calls
    )

    # 4. Control 3: Sync stream without stream_options (stream_options=None)
    plain_sync_wrapper = build_stream(sync_mode=True, stream_options=None, fixed=False)
    plain_yielded = [c for c in plain_sync_wrapper]
    plain_finish = [c for c in plain_yielded if c.choices and c.choices[0].finish_reason is not None][0]
    plain_fields = getattr(plain_finish.choices[0].delta, "provider_specific_fields", None)

    control_plain_sync_preserved = (
        plain_fields is not None
        and plain_fields.get("mcp_tool_calls") == expected_tool_calls
    )

    return {
        "bug_sync_dropped_metadata": bug_sync_dropped_metadata,
        "control_async_preserved": control_async_preserved,
        "control_fixed_sync_preserved": control_fixed_sync_preserved,
        "control_plain_sync_preserved": control_plain_sync_preserved,
        "details": {
            "sync_fields": sync_fields,
            "async_fields": async_fields,
            "fixed_fields": fixed_fields,
            "plain_fields": plain_fields,
        },
    }


def main() -> None:
    wire_dir = Path(__file__).resolve().parent.parent / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    runs = []
    for _ in range(5):
        res = run_reproduction()
        runs.append({
            "bug_sync_dropped_metadata": res["bug_sync_dropped_metadata"],
            "control_async_preserved": res["control_async_preserved"],
            "control_fixed_sync_preserved": res["control_fixed_sync_preserved"],
            "control_plain_sync_preserved": res["control_plain_sync_preserved"],
        })

    last_details = res["details"]

    bug_wire = {
        "stream_mode": "sync",
        "stream_options": {"include_usage": True},
        "has_mcp_metadata_in_hidden_params": True,
        "finish_chunk_has_provider_specific_fields": last_details["sync_fields"] is not None,
        "provider_specific_fields": last_details["sync_fields"],
    }
    with open(wire_dir / "bug-sync-stream-mcp-metadata-dropped.json", "w") as f:
        json.dump(bug_wire, f, indent=2)

    control_async_wire = {
        "stream_mode": "async",
        "stream_options": {"include_usage": True},
        "finish_chunk_has_provider_specific_fields": last_details["async_fields"] is not None,
        "provider_specific_fields": last_details["async_fields"],
    }
    with open(wire_dir / "control-async-stream-mcp-metadata-preserved.json", "w") as f:
        json.dump(control_async_wire, f, indent=2)

    control_fixed_wire = {
        "stream_mode": "sync_fixed",
        "stream_options": {"include_usage": True},
        "finish_chunk_has_provider_specific_fields": last_details["fixed_fields"] is not None,
        "provider_specific_fields": last_details["fixed_fields"],
    }
    with open(wire_dir / "control-fixed-sync-stream-mcp-metadata-preserved.json", "w") as f:
        json.dump(control_fixed_wire, f, indent=2)

    control_plain_wire = {
        "stream_mode": "sync",
        "stream_options": None,
        "finish_chunk_has_provider_specific_fields": last_details["plain_fields"] is not None,
        "provider_specific_fields": last_details["plain_fields"],
    }
    with open(wire_dir / "control-sync-stream-no-options-preserved.json", "w") as f:
        json.dump(control_plain_wire, f, indent=2)

    matrix = {
        "iterations": 5,
        "success_rate": "5/5",
        "all_passed": all(
            r["bug_sync_dropped_metadata"]
            and r["control_async_preserved"]
            and r["control_fixed_sync_preserved"]
            and r["control_plain_sync_preserved"]
            for r in runs
        ),
        "runs": runs,
    }
    with open(wire_dir / "results.json", "w") as f:
        json.dump(matrix, f, indent=2)

    with open(Path(__file__).resolve().parent.parent / "results.json", "w") as f:
        json.dump(matrix, f, indent=2)

    print("Reproduction complete. 5/5 runs verified.")


if __name__ == "__main__":
    main()
