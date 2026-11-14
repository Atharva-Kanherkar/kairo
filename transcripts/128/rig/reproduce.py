"""Reproduction script for LiteLLM provider fake streams dropping logprobs.

Demonstrates that when chat completions requests are replayed as provider fake streams
(e.g. when using structured outputs/response_format or providers without native streaming support):
1. In `litellm/llms/base_llm/base_model_iterator.py:convert_model_response_to_streaming`,
   `StreamingChoices` is instantiated without passing `logprobs`.
2. When the provider returns HTTP 200 with complete `logprobs` metadata for choices,
   the non-streaming response (`stream=False`) preserves token log probabilities.
3. The streamed response (`stream=True`) through the provider fake stream strips `logprobs`
   to `None` across all chunks, causing streamed vs non-streamed results to disagree.
4. Controls:
   - Non-streaming call (`stream=False, logprobs=True`) retains full token log probabilities.
   - Fixed stream conversion (PR #49405 logic with `preserve_logprobs=True`) retains matching
     choice logprobs on the streaming chunks.
   - Streamed call with `logprobs=False` yields `None` (as expected).
"""

from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, cast

import litellm
from litellm.llms.base_llm.base_model_iterator import (
    _streaming_delta,
    convert_model_response_to_streaming,
)
from litellm.types.utils import (
    ChoiceLogprobs,
    Choices,
    Delta,
    ModelResponse,
    ModelResponseStream,
    StreamingChoices,
)


_MOCK_LOGPROBS_PAYLOAD = {
    "content": [
        {
            "token": "Paris",
            "logprob": -0.25,
            "bytes": [80, 97, 114, 105, 115],
            "top_logprobs": [
                {"token": "Paris", "logprob": -0.25, "bytes": [80, 97, 114, 105, 115]},
                {"token": "London", "logprob": -1.75, "bytes": [76, 111, 110, 100, 111, 110]},
            ],
        }
    ],
    "refusal": [],
}


class MockOpenAIProviderServer(http.server.BaseHTTPRequestHandler):
    received_requests: list[dict[str, Any]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length).decode("utf-8")
        req_json = json.loads(body) if body else {}
        MockOpenAIProviderServer.received_requests.append(req_json)

        has_logprobs = req_json.get("logprobs") is True

        response_body = {
            "id": "chatcmpl-fake-stream-logprobs",
            "object": "chat.completion",
            "created": 1730419200,
            "model": "llama3-8b-8192",
            "system_fingerprint": "fp_test",
            "service_tier": None,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"city": "Paris"}',
                    },
                    "finish_reason": "stop",
                    "logprobs": _MOCK_LOGPROBS_PAYLOAD if has_logprobs else None,
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 6,
                "total_tokens": 16,
            },
        }

        encoded = json.dumps(response_body).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def fixed_convert_model_response_to_streaming(
    model_response: ModelResponse,
    *,
    preserve_logprobs: bool = False,
) -> ModelResponseStream:
    """Implementation of PR #49405 fix."""
    streaming_choices: list[StreamingChoices] = []
    for choice in model_response.choices:
        streaming_choices.append(
            StreamingChoices(
                index=choice.index,
                delta=_streaming_delta(cast(Choices, choice).message),
                finish_reason=choice.finish_reason,
                logprobs=getattr(choice, "logprobs", None) if preserve_logprobs else None,
            )
        )
    processed_chunk = ModelResponseStream(
        id=model_response.id,
        object="chat.completion.chunk",
        created=model_response.created,
        model=model_response.model,
        choices=streaming_choices,
    )
    usage = getattr(model_response, "usage", None)
    if usage is not None:
        setattr(processed_chunk, "usage", usage)
    return processed_chunk


def run_iteration(
    run_idx: int,
    base_url: str,
    wire_dir: Path | None = None,
) -> dict[str, bool]:
    MockOpenAIProviderServer.received_requests.clear()

    # 1. Failing case: stream=True, logprobs=True, fake_stream route (structured outputs)
    stream_response = litellm.completion(
        model="groq/llama3-8b-8192",
        messages=[{"role": "user", "content": "Where is the Eiffel Tower located?"}],
        api_base=base_url,
        api_key="fake-test-key",
        logprobs=True,
        response_format={"type": "json_object"},
        stream=True,
    )
    chunks = list(stream_response)
    # The provider returned logprobs, but fake_stream chunks omit/nullify logprobs
    bug_logprobs_dropped = len(chunks) > 0 and chunks[0].choices[0].logprobs is None

    # 2. Control 1: Non-streaming call with identical inputs
    sync_response = litellm.completion(
        model="groq/llama3-8b-8192",
        messages=[{"role": "user", "content": "Where is the Eiffel Tower located?"}],
        api_base=base_url,
        api_key="fake-test-key",
        logprobs=True,
        response_format={"type": "json_object"},
        stream=False,
    )
    ctrl_sync_logprobs_preserved = (
        sync_response.choices[0].logprobs is not None
        and sync_response.choices[0].logprobs.content[0].token == "Paris"
        and sync_response.choices[0].logprobs.content[0].logprob == -0.25
    )

    # 3. Control 2: Fixed PR #49405 stream conversion with preserve_logprobs=True
    fixed_chunk = fixed_convert_model_response_to_streaming(
        sync_response, preserve_logprobs=True
    )
    ctrl_fixed_logprobs_preserved = (
        fixed_chunk.choices[0].logprobs is not None
        and fixed_chunk.choices[0].logprobs == sync_response.choices[0].logprobs
    )

    # 4. Control 3: Streamed call with logprobs=False correctly yields None
    stream_no_logprobs = litellm.completion(
        model="groq/llama3-8b-8192",
        messages=[{"role": "user", "content": "Where is the Eiffel Tower located?"}],
        api_base=base_url,
        api_key="fake-test-key",
        logprobs=False,
        response_format={"type": "json_object"},
        stream=True,
    )
    chunks_no_logprobs = list(stream_no_logprobs)
    ctrl_no_logprobs_null = (
        len(chunks_no_logprobs) > 0
        and chunks_no_logprobs[0].choices[0].logprobs is None
    )

    if wire_dir is not None and run_idx == 1:
        # Save raw evidence
        bug_wire = {
            "test_case": "bug_streaming_fake_stream_drops_logprobs",
            "model": "groq/llama3-8b-8192",
            "request_flags": {
                "stream": True,
                "logprobs": True,
                "response_format": {"type": "json_object"},
            },
            "upstream_returned_logprobs": True,
            "chunk_count": len(chunks),
            "chunk_choice_logprobs": chunks[0].choices[0].logprobs,
            "first_chunk": chunks[0].model_dump(),
        }
        (wire_dir / "bug-streaming-logprobs-dropped.json").write_text(
            json.dumps(bug_wire, indent=2)
        )

        ctrl_sync_wire = {
            "test_case": "control_sync_logprobs_preserved",
            "model": "groq/llama3-8b-8192",
            "request_flags": {
                "stream": False,
                "logprobs": True,
                "response_format": {"type": "json_object"},
            },
            "response_choice_logprobs": sync_response.choices[0].logprobs.model_dump(),
        }
        (wire_dir / "control-sync-logprobs-preserved.json").write_text(
            json.dumps(ctrl_sync_wire, indent=2)
        )

        ctrl_fixed_wire = {
            "test_case": "control_fixed_fake_stream_logprobs_preserved",
            "model": "groq/llama3-8b-8192",
            "pr_49405_fix": "preserve_logprobs=True on provider fake stream",
            "chunk_choice_logprobs": fixed_chunk.choices[0].logprobs.model_dump(),
            "matches_sync_response": ctrl_fixed_logprobs_preserved,
        }
        (wire_dir / "control-fixed-fake-stream-logprobs-preserved.json").write_text(
            json.dumps(ctrl_fixed_wire, indent=2)
        )

        ctrl_no_logprobs_wire = {
            "test_case": "control_streaming_logprobs_false_null",
            "model": "groq/llama3-8b-8192",
            "request_flags": {
                "stream": True,
                "logprobs": False,
            },
            "chunk_choice_logprobs": chunks_no_logprobs[0].choices[0].logprobs,
        }
        (wire_dir / "control-streaming-logprobs-false-null.json").write_text(
            json.dumps(ctrl_no_logprobs_wire, indent=2)
        )

        provider_wire = {
            "provider_response_fixture": {
                "id": "chatcmpl-fake-stream-logprobs",
                "object": "chat.completion",
                "model": "llama3-8b-8192",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "logprobs": _MOCK_LOGPROBS_PAYLOAD,
                    }
                ],
            }
        }
        (wire_dir / "provider-upstream-response.json").write_text(
            json.dumps(provider_wire, indent=2)
        )

    return {
        "bug_logprobs_dropped": bug_logprobs_dropped,
        "ctrl_sync_logprobs_preserved": ctrl_sync_logprobs_preserved,
        "ctrl_fixed_logprobs_preserved": ctrl_fixed_logprobs_preserved,
        "ctrl_no_logprobs_null": ctrl_no_logprobs_null,
    }


def main() -> None:
    port = 9960
    server = http.server.HTTPServer(("127.0.0.1", port), MockOpenAIProviderServer)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    rig_dir = Path(__file__).resolve().parent
    transcripts_dir = rig_dir.parent
    wire_dir = transcripts_dir / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    base_url = f"http://127.0.0.1:{port}"
    iterations = 5
    results: list[dict[str, bool]] = []

    try:
        for i in range(iterations):
            res = run_iteration(
                i + 1, base_url=base_url, wire_dir=wire_dir if i == 0 else None
            )
            results.append(res)
            print(f"Run {i+1}/{iterations}: {res}")
    finally:
        server.shutdown()

    all_passed = all(
        r["bug_logprobs_dropped"]
        and r["ctrl_sync_logprobs_preserved"]
        and r["ctrl_fixed_logprobs_preserved"]
        and r["ctrl_no_logprobs_null"]
        for r in results
    )

    payload = {
        "iterations": iterations,
        "success_rate": f"{sum(1 for r in results if all(r.values()))}/{iterations}",
        "all_passed": all_passed,
        "runs": results,
    }

    (transcripts_dir / "results.json").write_text(json.dumps(payload, indent=2))
    (wire_dir / "results.json").write_text(json.dumps(payload, indent=2))
    print(f"\nFinal status: {payload['success_rate']} passed. results.json written.")


if __name__ == "__main__":
    main()
