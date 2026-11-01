#!/usr/bin/env python3
"""Reproduction rig for Finding 141: LiteLLM Cohere v2 Non-Streaming Finish Reason Loss

Verifies that in unpatched LiteLLM (v1.109.0):
1. Defect (Tool Call): When Cohere v2 returns finish_reason="TOOL_CALL" and tool_calls,
   unpatched CohereV2ChatConfig.transform_response omits mapping finish_reason,
   defaulting choices[0].finish_reason to "stop". Agent loops expecting "tool_calls"
   halt without executing tools.
2. Defect (Max Tokens): When Cohere v2 returns finish_reason="MAX_TOKENS", unpatched LiteLLM
   returns choices[0].finish_reason="stop" instead of "length", hiding truncation from callers.
3. Control 1 (Fixed PR #49529): Patched transform_response maps finish_reason via map_finish_reason,
   yielding "tool_calls" on TOOL_CALL and "length" on MAX_TOKENS.
4. Control 2 (Complete): Cohere v2 finish_reason="COMPLETE" returns choices[0].finish_reason="stop".
5. Control 3 (Stop Sequence): Cohere v2 finish_reason="STOP_SEQUENCE" returns choices[0].finish_reason="stop".
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
from litellm.litellm_core_utils.core_helpers import _FINISH_REASON_MAP, map_finish_reason
from litellm.llms.cohere.chat.v2_transformation import CohereV2ChatConfig

# Ensure TOOL_CALL mapping is registered in core_helpers as in upstream main
_FINISH_REASON_MAP["TOOL_CALL"] = "tool_calls"

COHERE_V2_CHAT_URL = "https://api.cohere.com/v2/chat"

TOOL_CALL_RAW_RESPONSE = {
    "id": "c14c80c3-18eb-4519-9460-6c92edd8cfb4",
    "finish_reason": "TOOL_CALL",
    "message": {
        "role": "assistant",
        "tool_plan": "I will look up the weather in New York.",
        "tool_calls": [
            {
                "id": "get_weather_k9q2xw1n",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location":"New York, NY"}',
                },
            }
        ],
    },
    "usage": {
        "billed_units": {"input_tokens": 17, "output_tokens": 9},
        "tokens": {"input_tokens": 211, "output_tokens": 9},
    },
}

MAX_TOKENS_RAW_RESPONSE = {
    "id": "c14c80c3-18eb-4519-9460-6c92edd8cfb5",
    "finish_reason": "MAX_TOKENS",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "This response was truncated before finishing."}],
    },
    "usage": {
        "billed_units": {"input_tokens": 15, "output_tokens": 50},
        "tokens": {"input_tokens": 150, "output_tokens": 50},
    },
}

COMPLETE_RAW_RESPONSE = {
    "id": "c14c80c3-18eb-4519-9460-6c92edd8cfb6",
    "finish_reason": "COMPLETE",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "The weather in New York is 65 degrees and clear."}],
    },
    "usage": {
        "billed_units": {"input_tokens": 10, "output_tokens": 15},
        "tokens": {"input_tokens": 120, "output_tokens": 15},
    },
}

STOP_SEQUENCE_RAW_RESPONSE = {
    "id": "c14c80c3-18eb-4519-9460-6c92edd8cfb7",
    "finish_reason": "STOP_SEQUENCE",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "Completed until custom stop token."}],
    },
    "usage": {
        "billed_units": {"input_tokens": 10, "output_tokens": 12},
        "tokens": {"input_tokens": 120, "output_tokens": 12},
    },
}

TEST_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather in location",
        "parameters": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    },
}


def run_unpatched(raw_json, tools=None):
    """Run non-streaming completion with unpatched CohereV2ChatConfig."""
    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.post(COHERE_V2_CHAT_URL).mock(
            return_value=httpx.Response(200, json=raw_json)
        )
        kwargs = {
            "model": "cohere_chat/v2/command-a-03-2025",
            "messages": [{"role": "user", "content": "Test query"}],
            "api_key": "cohere-test-key",
            "stream": False,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = litellm.completion(**kwargs)
        choice = response.choices[0]
        return {
            "provider_finish_reason": raw_json["finish_reason"],
            "response_finish_reason": choice.finish_reason,
            "has_tool_calls": bool(choice.message.tool_calls),
            "tool_calls_count": len(choice.message.tool_calls) if choice.message.tool_calls else 0,
        }


def run_fixed(raw_json, tools=None):
    """Run non-streaming completion with PR #49529 patched transform_response."""
    orig_transform = CohereV2ChatConfig.transform_response

    def patched_transform(self, *args, **kwargs):
        res = orig_transform(self, *args, **kwargs)
        raw_response = kwargs.get("raw_response") if "raw_response" in kwargs else args[1]
        cohere_resp = raw_response.json()
        res.choices[0].finish_reason = map_finish_reason(
            cohere_resp.get("finish_reason", "COMPLETE")
        )
        return res

    CohereV2ChatConfig.transform_response = patched_transform
    try:
        with respx.mock(assert_all_called=False) as respx_mock:
            respx_mock.post(COHERE_V2_CHAT_URL).mock(
                return_value=httpx.Response(200, json=raw_json)
            )
            kwargs = {
                "model": "cohere_chat/v2/command-a-03-2025",
                "messages": [{"role": "user", "content": "Test query"}],
                "api_key": "cohere-test-key",
                "stream": False,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = litellm.completion(**kwargs)
            choice = response.choices[0]
            return {
                "provider_finish_reason": raw_json["finish_reason"],
                "response_finish_reason": choice.finish_reason,
                "has_tool_calls": bool(choice.message.tool_calls),
                "tool_calls_count": len(choice.message.tool_calls) if choice.message.tool_calls else 0,
            }
    finally:
        CohereV2ChatConfig.transform_response = orig_transform


def main():
    print("Executing Finding 141 reproduction rig (N=5 runs)...")
    runs = []
    wire_dir = Path("/Users/atharva/kairo-141/transcripts/141/wire")
    wire_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, 6):
        print(f"Run {i}/5...")
        # Defect 1: TOOL_CALL returns "stop"
        defect_tc = run_unpatched(TOOL_CALL_RAW_RESPONSE, tools=[TEST_TOOL])
        # Defect 2: MAX_TOKENS returns "stop"
        defect_mt = run_unpatched(MAX_TOKENS_RAW_RESPONSE)

        # Control 1: Patched TOOL_CALL returns "tool_calls"
        fixed_tc = run_fixed(TOOL_CALL_RAW_RESPONSE, tools=[TEST_TOOL])
        # Control 2: Patched MAX_TOKENS returns "length"
        fixed_mt = run_fixed(MAX_TOKENS_RAW_RESPONSE)

        # Control 3: COMPLETE returns "stop"
        ctrl_complete = run_unpatched(COMPLETE_RAW_RESPONSE)
        # Control 4: STOP_SEQUENCE returns "stop"
        ctrl_stop_seq = run_unpatched(STOP_SEQUENCE_RAW_RESPONSE)

        assert defect_tc["response_finish_reason"] == "stop" and defect_tc["has_tool_calls"], (
            f"Run {i}: Defect 1 (TOOL_CALL) did not reproduce!"
        )
        assert defect_mt["response_finish_reason"] == "stop", (
            f"Run {i}: Defect 2 (MAX_TOKENS) did not reproduce!"
        )
        assert fixed_tc["response_finish_reason"] == "tool_calls", (
            f"Run {i}: Control 1 (fixed TOOL_CALL) failed!"
        )
        assert fixed_mt["response_finish_reason"] == "length", (
            f"Run {i}: Control 2 (fixed MAX_TOKENS) failed!"
        )
        assert ctrl_complete["response_finish_reason"] == "stop", (
            f"Run {i}: Control 3 (COMPLETE) failed!"
        )
        assert ctrl_stop_seq["response_finish_reason"] == "stop", (
            f"Run {i}: Control 4 (STOP_SEQUENCE) failed!"
        )

        run_summary = {
            "run": i,
            "defect_unpatched_tool_call_reports_stop": defect_tc["response_finish_reason"] == "stop",
            "defect_unpatched_max_tokens_reports_stop": defect_mt["response_finish_reason"] == "stop",
            "control_fixed_tool_call_reports_tool_calls": fixed_tc["response_finish_reason"] == "tool_calls",
            "control_fixed_max_tokens_reports_length": fixed_mt["response_finish_reason"] == "length",
            "control_complete_reports_stop": ctrl_complete["response_finish_reason"] == "stop",
            "control_stop_sequence_reports_stop": ctrl_stop_seq["response_finish_reason"] == "stop",
        }
        runs.append(run_summary)

        if i == 1:
            # Save wire captures
            with open(wire_dir / "bug-cohere-v2-tool-call-finish-reason-stop.json", "w") as f:
                json.dump(
                    {
                        "description": "LiteLLM Cohere v2 returns stop for tool call response instead of tool_calls",
                        "target": "LiteLLM v1.109.0",
                        "cohere_raw_response": TOOL_CALL_RAW_RESPONSE,
                        **defect_tc,
                    },
                    f,
                    indent=2,
                )

            with open(wire_dir / "bug-cohere-v2-max-tokens-finish-reason-stop.json", "w") as f:
                json.dump(
                    {
                        "description": "LiteLLM Cohere v2 returns stop for truncated response instead of length",
                        "target": "LiteLLM v1.109.0",
                        "cohere_raw_response": MAX_TOKENS_RAW_RESPONSE,
                        **defect_mt,
                    },
                    f,
                    indent=2,
                )

            with open(wire_dir / "control-fixed-cohere-v2-tool-calls.json", "w") as f:
                json.dump(
                    {
                        "description": "Fixed Cohere v2 response mapping finish_reason TOOL_CALL to tool_calls (PR #49529)",
                        "target": "LiteLLM with PR #49529 fix",
                        "cohere_raw_response": TOOL_CALL_RAW_RESPONSE,
                        **fixed_tc,
                    },
                    f,
                    indent=2,
                )

            with open(wire_dir / "control-fixed-cohere-v2-max-tokens.json", "w") as f:
                json.dump(
                    {
                        "description": "Fixed Cohere v2 response mapping finish_reason MAX_TOKENS to length (PR #49529)",
                        "target": "LiteLLM with PR #49529 fix",
                        "cohere_raw_response": MAX_TOKENS_RAW_RESPONSE,
                        **fixed_mt,
                    },
                    f,
                    indent=2,
                )

            with open(wire_dir / "control-cohere-v2-complete.json", "w") as f:
                json.dump(
                    {
                        "description": "Control: Cohere v2 COMPLETE correctly maps to stop",
                        "target": "LiteLLM v1.109.0",
                        "cohere_raw_response": COMPLETE_RAW_RESPONSE,
                        **ctrl_complete,
                    },
                    f,
                    indent=2,
                )

    matrix = {
        "finding": 141,
        "name": "litellm-cohere-v2-nonstream-finish-reason",
        "target": "LiteLLM v1.109.0 (commit cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a)",
        "upstream_pr": "https://github.com/BerriAI/litellm/pull/49529",
        "upstream_ticket": "LIT-9731",
        "total_runs": 5,
        "reproduction_rate": "5/5 (100%)",
        "runs": runs,
    }

    with open(wire_dir / "results.json", "w") as f:
        json.dump(matrix, f, indent=2)

    with open(Path("/Users/atharva/kairo-141/transcripts/141/results.json"), "w") as f:
        json.dump(matrix, f, indent=2)

    print("Reproduction matrix complete! Wire captures and results saved.")


if __name__ == "__main__":
    main()
