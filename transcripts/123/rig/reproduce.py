#!/usr/bin/env python3
"""Reproduction rig for Finding 123:

LiteLLM /v1/messages drops encrypted reasoning when routing through the chat completions
bridge for chatgpt/ and Responses providers, preventing multi-turn reasoning replay.
"""

import json
import os
import sys
from pathlib import Path

from litellm.completion_extras.litellm_responses_transformation.transformation import (
    LiteLLMResponsesTransformationHandler,
)
from litellm.llms.anthropic.pass_through.adapters.transformation import (
    LiteLLMAnthropicMessagesAdapter,
)
from litellm.llms.anthropic.pass_through.responses_adapters.transformation import (
    LiteLLMAnthropicToResponsesAPIAdapter,
)
from litellm.types.llms.openai import ResponsesAPIResponse
from litellm.types.utils import ModelResponse, Usage


ENCRYPTED_REASONING_TOKEN = "gAAAAABn1234567890abcdef_secret_reasoning_payload"


def run_iteration(iteration: int, output_dir: Path) -> dict[str, bool]:
    # -------------------------------------------------------------
    # 1. Outbound Response: Backend emits reasoning item
    # -------------------------------------------------------------
    raw_response_dict = {
        "id": f"resp_091186a8_{iteration}",
        "object": "response",
        "status": "completed",
        "created_at": 1730450000 + iteration,
        "model": "gpt-6.1-sol",
        "output": [
            {
                "type": "reasoning",
                "id": f"rs_091186a8_{iteration}",
                "summary": [],
                "content": [],
                "encrypted_content": ENCRYPTED_REASONING_TOKEN,
            },
            {
                "type": "function_call",
                "id": f"fc_091186a8_{iteration}",
                "call_id": "call_44a2f3c095ab",
                "name": "get_weather",
                "arguments": '{"city": "Oslo"}',
            },
        ],
        "usage": {
            "input_tokens": 15,
            "output_tokens": 30,
            "total_tokens": 45,
        },
    }
    raw_resp = ResponsesAPIResponse(**raw_response_dict)

    # Control Outbound: Direct Responses adapter preserves reasoning as redacted_thinking
    ctrl_responses_adapter = LiteLLMAnthropicToResponsesAPIAdapter()
    ctrl_outbound_dict = ctrl_responses_adapter.translate_response(raw_resp)
    ctrl_outbound_blocks = ctrl_outbound_dict.get("content", [])
    ctrl_has_thinking = any(
        b.get("type") in ("thinking", "redacted_thinking")
        and ENCRYPTED_REASONING_TOKEN in (b.get("data") or b.get("signature") or "")
        for b in ctrl_outbound_blocks
    )

    # Defect Outbound: Chat completions bridge drops reasoning_items
    bridge_handler = LiteLLMResponsesTransformationHandler()
    chat_model_resp = bridge_handler.transform_response(
        model="chatgpt/gpt-6.1-sol",
        raw_response=raw_resp,
        model_response=ModelResponse(),
        logging_obj=None,
        request_data={},
        messages=[],
        optional_params={},
        litellm_params={},
        encoding=None,
    )
    chat_adapter = LiteLLMAnthropicMessagesAdapter()
    setattr(chat_model_resp, "usage", Usage(prompt_tokens=15, completion_tokens=30, total_tokens=45))
    defect_outbound_resp = chat_adapter.translate_openai_response_to_anthropic(chat_model_resp)
    defect_outbound_blocks = defect_outbound_resp.get("content", [])
    defect_has_thinking = any(
        b.get("type") in ("thinking", "redacted_thinking")
        for b in defect_outbound_blocks
    )
    # Defect verified if thinking block is missing outbound
    bug_outbound_dropped = not defect_has_thinking

    # -------------------------------------------------------------
    # 2. Inbound Request: Client replays reasoning block in next turn
    # -------------------------------------------------------------
    anthropic_messages = [
        {
            "role": "user",
            "content": "Think about whether Madrid or Oslo is colder in January, then call get_weather.",
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "",
                    "signature": f"litellm_encrypted_reasoning:{ENCRYPTED_REASONING_TOKEN}",
                },
                {
                    "type": "tool_use",
                    "id": "call_44a2f3c095ab",
                    "name": "get_weather",
                    "input": {"city": "Oslo"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_44a2f3c095ab",
                    "content": "-7 C, snow",
                }
            ],
        },
    ]

    # Control Inbound: Direct adapter preserves reasoning item into Responses input
    ctrl_input_items = ctrl_responses_adapter.translate_messages_to_responses_input(anthropic_messages)
    ctrl_inbound_replayed = any(
        item.get("type") == "reasoning"
        and item.get("encrypted_content") == ENCRYPTED_REASONING_TOKEN
        for item in ctrl_input_items
    )

    # Defect Inbound: Chat adapter unconditionally strips encrypted reasoning blocks
    openai_chat_messages = chat_adapter.translate_anthropic_messages_to_openai(
        anthropic_messages,
        model="chatgpt/gpt-6.1-sol",
    )
    defect_input_items, _ = bridge_handler._convert_chat_completion_messages_to_responses_input(openai_chat_messages)
    defect_inbound_has_reasoning = any(
        item.get("type") == "reasoning"
        for item in defect_input_items
    )
    # Defect verified if reasoning item is missing inbound
    bug_inbound_stripped = not defect_inbound_has_reasoning

    if iteration == 1:
        # Write wire captures
        (output_dir / "control-outbound-reasoning-preserved.json").write_text(
            json.dumps(ctrl_outbound_dict, indent=2) + "\n"
        )
        (output_dir / "bug-outbound-reasoning-items-dropped.json").write_text(
            json.dumps(defect_outbound_resp, indent=2) + "\n"
        )
        (output_dir / "control-inbound-reasoning-replayed.json").write_text(
            json.dumps(
                {
                    "endpoint": "chatgpt.com/backend-api/codex/responses",
                    "input_items": ctrl_input_items,
                },
                indent=2,
            )
            + "\n"
        )
        (output_dir / "bug-inbound-reasoning-stripped.json").write_text(
            json.dumps(
                {
                    "endpoint": "chatgpt.com/backend-api/codex/responses",
                    "input_items": defect_input_items,
                },
                indent=2,
            )
            + "\n"
        )

    return {
        "ctrl_outbound_preserved": ctrl_has_thinking,
        "bug_outbound_dropped": bug_outbound_dropped,
        "ctrl_inbound_replayed": ctrl_inbound_replayed,
        "bug_inbound_stripped": bug_inbound_stripped,
    }


def main():
    root = Path(__file__).resolve().parent.parent
    wire_dir = root / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    iterations = 5
    results = []
    print(f"Running reproduction across {iterations} iterations...")
    for i in range(1, iterations + 1):
        res = run_iteration(i, wire_dir)
        results.append(res)
        print(
            f"Iteration {i}: "
            f"Ctrl Outbound Preserved={res['ctrl_outbound_preserved']}, "
            f"Bug Outbound Dropped={res['bug_outbound_dropped']}, "
            f"Ctrl Inbound Replayed={res['ctrl_inbound_replayed']}, "
            f"Bug Inbound Stripped={res['bug_inbound_stripped']}"
        )

    all_passed = all(
        r["ctrl_outbound_preserved"]
        and r["bug_outbound_dropped"]
        and r["ctrl_inbound_replayed"]
        and r["bug_inbound_stripped"]
        for r in results
    )

    summary = {
        "iterations": iterations,
        "success_rate": f"{sum(1 for r in results if r['ctrl_outbound_preserved'] and r['bug_outbound_dropped'] and r['ctrl_inbound_replayed'] and r['bug_inbound_stripped'])}/{iterations}",
        "all_passed": all_passed,
        "runs": results,
    }
    (root / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Wire evidence written to {wire_dir}")
    print(f"Results summary written to {root / 'results.json'}")

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
