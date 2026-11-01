#!/usr/bin/env python3
"""Reproduction rig for Finding 140: LiteLLM OpenRouter Stream Served Model Loss

Verifies that in unpatched LiteLLM (v1.109.0):
1. Defect: When streaming with an OpenRouter auto router alias (e.g. openrouter/auto),
   LiteLLM's CustomStreamWrapper unconditionally overwrites model_response.model = self.model,
   erasing the actual served model (e.g. openai/gpt-4o-mini) returned in upstream SSE chunks.
   Every emitted chunk and the stream_chunk_builder assembled response report model: "auto".
2. Control 1: Fixed streaming handler (under PR #49498 logic) preserves the served model
   reported by OpenRouter across stream chunks and the assembled response.
3. Control 2: Direct model requests (openrouter/openai/gpt-4o-mini) where requested and served
   models match preserve model identity.
4. Control 3: Non-streaming completion (stream=False) returns response.model == "openai/gpt-4o-mini",
   proving that the non-streaming path already preserves the served model and isolating the defect
   to the streaming handler.
"""

import json
import os
import sys
from pathlib import Path

# Add LiteLLM pinned checkout
sys.path.insert(0, "/Users/atharva/litellm-main")

import httpx
import respx
import litellm
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import LlmProviders

RAW_OPENROUTER_SSE_CHUNKS = (
    b"data: {\"id\":\"gen-openrouter-stream-001\",\"created\":1730000000,\"model\":null,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\"},\"finish_reason\":null}]}\n\n"
    b"data: {\"id\":\"gen-openrouter-stream-001\",\"created\":1730000000,\"model\":\"openai/gpt-4o-mini\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Hello \"},\"finish_reason\":null}],\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":2,\"total_tokens\":7}}\n\n"
    b"data: {\"id\":\"gen-openrouter-stream-001\",\"created\":1730000000,\"model\":\"openai/gpt-4o-mini\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"world!\"},\"finish_reason\":null}]}\n\n"
    b"data: {\"id\":\"gen-openrouter-stream-001\",\"created\":1730000000,\"model\":\"\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n"
    b"data: [DONE]\n\n"
)

RAW_OPENROUTER_DIRECT_SSE_CHUNKS = (
    b"data: {\"id\":\"gen-openrouter-stream-002\",\"created\":1730000000,\"model\":\"openai/gpt-4o-mini\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\"},\"finish_reason\":null}]}\n\n"
    b"data: {\"id\":\"gen-openrouter-stream-002\",\"created\":1730000000,\"model\":\"openai/gpt-4o-mini\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Direct response\"},\"finish_reason\":null}]}\n\n"
    b"data: {\"id\":\"gen-openrouter-stream-002\",\"created\":1730000000,\"model\":\"openai/gpt-4o-mini\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n"
    b"data: [DONE]\n\n"
)

NONSTREAMING_JSON_RESPONSE = {
    "id": "gen-openrouter-nonstream-001",
    "created": 1730000000,
    "model": "openai/gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello world!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}


def run_unpatched_stream():
    """Reproduce defect: unpatched CustomStreamWrapper overwrites model with self.model."""
    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=RAW_OPENROUTER_SSE_CHUNKS,
                headers={"Content-Type": "text/event-stream"},
            )
        )
        stream = litellm.completion(
            model="openrouter/auto",
            messages=[{"role": "user", "content": "Say hello"}],
            stream=True,
            api_key="sk-or-test-key",
        )
        chunks = list(stream)
        assembled = litellm.stream_chunk_builder(chunks=chunks)

        chunk_models = [c.model for c in chunks]
        return {
            "requested_model": "openrouter/auto",
            "provider_served_model": "openai/gpt-4o-mini",
            "emitted_chunk_models": chunk_models,
            "assembled_response_model": assembled.model,
            "assembled_content": assembled.choices[0].message.content,
            "served_model_lost": assembled.model == "auto" and all(m == "auto" for m in chunk_models),
        }


def run_fixed_stream():
    """Run Control 1: patched CustomStreamWrapper using PR #49498 logic preserves served model."""
    orig_chunk_creator = CustomStreamWrapper.chunk_creator

    def _served_openrouter_model(self):
        if self.custom_llm_provider == LlmProviders.OPENROUTER.value or (
            self.custom_llm_provider == "cached_response"
            and self.logging_obj.model_call_details.get("custom_llm_provider") == LlmProviders.OPENROUTER.value
        ):
            return self._provider_response_model
        return None

    CustomStreamWrapper._served_openrouter_model = _served_openrouter_model

    def patched_chunk_creator(self, chunk):
        res = orig_chunk_creator(self, chunk)
        if res is not None:
            served = self._served_openrouter_model()
            if served:
                res.model = served
        return res

    CustomStreamWrapper.chunk_creator = patched_chunk_creator

    try:
        with respx.mock(assert_all_called=False) as respx_mock:
            respx_mock.post("https://openrouter.ai/api/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    content=RAW_OPENROUTER_SSE_CHUNKS,
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            stream = litellm.completion(
                model="openrouter/auto",
                messages=[{"role": "user", "content": "Say hello"}],
                stream=True,
                api_key="sk-or-test-key",
            )
            chunks = list(stream)
            assembled = litellm.stream_chunk_builder(chunks=chunks)

            chunk_models = [c.model for c in chunks]
            return {
                "requested_model": "openrouter/auto",
                "provider_served_model": "openai/gpt-4o-mini",
                "emitted_chunk_models": chunk_models,
                "assembled_response_model": assembled.model,
                "assembled_content": assembled.choices[0].message.content,
                "served_model_preserved": assembled.model == "openai/gpt-4o-mini",
            }
    finally:
        CustomStreamWrapper.chunk_creator = orig_chunk_creator
        if hasattr(CustomStreamWrapper, "_served_openrouter_model"):
            delattr(CustomStreamWrapper, "_served_openrouter_model")


def run_direct_stream():
    """Run Control 2: direct model request preserves requested and served model."""
    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=RAW_OPENROUTER_DIRECT_SSE_CHUNKS,
                headers={"Content-Type": "text/event-stream"},
            )
        )
        stream = litellm.completion(
            model="openrouter/openai/gpt-4o-mini",
            messages=[{"role": "user", "content": "Direct test"}],
            stream=True,
            api_key="sk-or-test-key",
        )
        chunks = list(stream)
        assembled = litellm.stream_chunk_builder(chunks=chunks)

        chunk_models = [c.model for c in chunks]
        return {
            "requested_model": "openrouter/openai/gpt-4o-mini",
            "provider_served_model": "openai/gpt-4o-mini",
            "emitted_chunk_models": chunk_models,
            "assembled_response_model": assembled.model,
            "assembled_content": assembled.choices[0].message.content,
            "direct_model_preserved": assembled.model == "openai/gpt-4o-mini",
        }


def run_nonstreaming_completion():
    """Run Control 3: non-streaming completion already returns the served model in response.model."""
    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=NONSTREAMING_JSON_RESPONSE,
                headers={"Content-Type": "application/json"},
            )
        )
        resp = litellm.completion(
            model="openrouter/auto",
            messages=[{"role": "user", "content": "Say hello"}],
            stream=False,
            api_key="sk-or-test-key",
        )
        return {
            "requested_model": "openrouter/auto",
            "provider_served_model": "openai/gpt-4o-mini",
            "response_model": resp.model,
            "response_content": resp.choices[0].message.content,
            "nonstreaming_served_model_preserved": resp.model == "openai/gpt-4o-mini",
        }


def main():
    print("Executing Finding 140 reproduction rig (N=5 runs)...")
    runs = []
    wire_dir = Path("/Users/atharva/kairo-140/transcripts/140/wire")
    wire_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, 6):
        print(f"Run {i}/5...")
        defect_res = run_unpatched_stream()
        control_fixed_res = run_fixed_stream()
        control_direct_res = run_direct_stream()
        control_nonstream_res = run_nonstreaming_completion()

        assert defect_res["served_model_lost"], f"Run {i}: Defect did not reproduce!"
        assert control_fixed_res["served_model_preserved"], f"Run {i}: Control 1 (fixed) failed!"
        assert control_direct_res["direct_model_preserved"], f"Run {i}: Control 2 (direct) failed!"
        assert control_nonstream_res["nonstreaming_served_model_preserved"], f"Run {i}: Control 3 (nonstreaming) failed!"

        run_summary = {
            "run": i,
            "defect_unpatched_stream_loses_served_model": defect_res["served_model_lost"],
            "control_fixed_stream_preserves_served_model": control_fixed_res["served_model_preserved"],
            "control_direct_stream_preserves_model": control_direct_res["direct_model_preserved"],
            "control_nonstreaming_preserves_served_model": control_nonstream_res["nonstreaming_served_model_preserved"],
        }
        runs.append(run_summary)

        # Save individual run captures for run 1
        if i == 1:
            bug_capture = {
                "description": "LiteLLM CustomStreamWrapper overwrites OpenRouter served model with requested router alias",
                "target": "LiteLLM v1.109.0 (commit cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a)",
                "raw_upstream_sse_bytes": RAW_OPENROUTER_SSE_CHUNKS.decode("utf-8"),
                **defect_res,
            }
            with open(wire_dir / "bug-stream-served-model-lost.json", "w") as f:
                json.dump(bug_capture, f, indent=2)

            fixed_capture = {
                "description": "Fixed CustomStreamWrapper preserving OpenRouter served model (PR #49498)",
                "target": "LiteLLM with PR #49498 streaming handler fix",
                "raw_upstream_sse_bytes": RAW_OPENROUTER_SSE_CHUNKS.decode("utf-8"),
                **control_fixed_res,
            }
            with open(wire_dir / "control-fixed-stream-served-model-preserved.json", "w") as f:
                json.dump(fixed_capture, f, indent=2)

            direct_capture = {
                "description": "Control: direct OpenRouter stream where requested model matches served model",
                "target": "LiteLLM v1.109.0",
                "raw_upstream_sse_bytes": RAW_OPENROUTER_DIRECT_SSE_CHUNKS.decode("utf-8"),
                **control_direct_res,
            }
            with open(wire_dir / "control-direct-stream-model.json", "w") as f:
                json.dump(direct_capture, f, indent=2)

            nonstream_capture = {
                "description": "Control: non-streaming OpenRouter completion correctly preserves served model",
                "target": "LiteLLM v1.109.0",
                "raw_upstream_response": NONSTREAMING_JSON_RESPONSE,
                **control_nonstream_res,
            }
            with open(wire_dir / "control-nonstreaming-served-model-preserved.json", "w") as f:
                json.dump(nonstream_capture, f, indent=2)

    matrix = {
        "finding": 140,
        "name": "litellm-openrouter-stream-served-model-loss",
        "target": "LiteLLM v1.109.0 (commit cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a)",
        "upstream_pr": "https://github.com/BerriAI/litellm/pull/49498",
        "upstream_issue": "https://github.com/BerriAI/litellm/issues/46095",
        "upstream_commit": "36dbb228d94a3e180fee68862f1b20be7bc45ebd",
        "total_runs": 5,
        "reproduction_rate": "5/5 (100%)",
        "runs": runs,
    }

    with open(wire_dir / "results.json", "w") as f:
        json.dump(matrix, f, indent=2)

    with open(Path("/Users/atharva/kairo-140/transcripts/140/results.json"), "w") as f:
        json.dump(matrix, f, indent=2)

    print("Reproduction matrix complete! Wire captures and results saved.")


if __name__ == "__main__":
    main()
