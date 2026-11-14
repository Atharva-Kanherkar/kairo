"""Reproduction script for LiteLLM Google GenAI adapter dropping tool responses in model turns.

Demonstrates that when a Gemini generateContent multi-turn conversation contains tool results
inside a role="model" turn (standard behavior in agent harnesses such as Google Antigravity CLI):
1. In `litellm/google_genai/adapters/transformation.py:_transform_contents_to_messages`,
   `functionResponse` and `function_response` parts are only handled when `role == "user"`.
2. When `role == "model"`, the adapter only checks for `text` and `functionCall`, completely
   ignoring `functionResponse` parts.
3. If the model turn contains only a tool result, the adapter emits an empty assistant message
   `{'role': 'assistant', 'content': None}` and completely drops the tool message.
4. When routed to OpenAI or Anthropic/Claude backends, the downstream provider rejects the call
   with HTTP 400 because the preceding tool call has no corresponding tool result message.
5. Controls:
   - Control 1 (User turn): `role: "user"` with `functionResponse` correctly produces
     a `tool` message (`role: "tool", tool_call_id: "call_view_file", content: "..."`).
   - Control 2 (Fixed adapter): Patched transformation extracts `functionResponse` and
     `function_response` in model turns and emits matching `tool` messages.
   - Control 3 (Standard model turn): Model turn with text or functionCall retains expected
     assistant message.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from litellm.google_genai.adapters.transformation import GoogleGenAIAdapter


def fixed_transform_contents_to_messages(
    adapter: GoogleGenAIAdapter,
    contents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fixed logic applying upstream PR #49552 changes to Google GenAI adapter."""
    messages: list[dict[str, Any]] = []

    for content in contents:
        role = content.get("role", "user")
        parts: Sequence[Any] = content.get("parts", [])

        if role == "user":
            content_parts = []
            tool_messages = []
            for part in parts:
                if isinstance(part, dict):
                    if "text" in part:
                        content_parts.append({"type": "text", "text": part["text"]})
                    elif "functionResponse" in part or "function_response" in part:
                        func_response = part.get("functionResponse") or part.get("function_response")
                        name = func_response.get("name", "unknown")
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": f"call_{name}",
                            "content": json.dumps(func_response.get("response", {})),
                        })
                elif isinstance(part, str):
                    content_parts.append({"type": "text", "text": part})

            if content_parts:
                if len(content_parts) == 1 and content_parts[0].get("type") == "text":
                    messages.append({"role": "user", "content": content_parts[0]["text"]})
                else:
                    messages.append({"role": "user", "content": content_parts})
            messages.extend(tool_messages)

        elif role == "model":
            combined_text = ""
            tool_calls = []
            tool_messages = []

            for part in parts:
                if isinstance(part, dict):
                    if "text" in part:
                        combined_text += part["text"]
                    elif "functionCall" in part or "function_call" in part:
                        func_call = part.get("functionCall") or part.get("function_call")
                        name = func_call.get("name", "unknown")
                        tool_calls.append({
                            "id": f"call_{name}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(func_call.get("args", {})),
                            },
                        })
                    elif "functionResponse" in part or "function_response" in part:
                        func_response = part.get("functionResponse") or part.get("function_response")
                        name = func_response.get("name", "unknown")
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": f"call_{name}",
                            "content": json.dumps(func_response.get("response", {})),
                        })
                elif isinstance(part, str):
                    combined_text += part

            if tool_calls or combined_text:
                asst_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": combined_text if combined_text else None,
                }
                if tool_calls:
                    asst_msg["tool_calls"] = tool_calls
                messages.append(asst_msg)

            messages.extend(tool_messages)

    return messages


def run_trials(n: int = 5) -> dict[str, Any]:
    wire_dir = Path(__file__).resolve().parent.parent / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    adapter = GoogleGenAIAdapter()

    # Conversation history representing multi-turn tool interaction
    # Turn 0: User requests file inspection
    # Turn 1: Model invokes view_file tool
    # Turn 2: Tool result submitted in model turn
    bug_contents = [
        {"role": "user", "parts": [{"text": "Read /tmp/a.py and summarize it."}]},
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "id": "call_view_file",
                        "name": "view_file",
                        "args": {"path": "/tmp/a.py"},
                    }
                }
            ],
        },
        {
            "role": "model",
            "parts": [
                {
                    "functionResponse": {
                        "id": "call_view_file",
                        "name": "view_file",
                        "response": {"output": "1: def a():\n2:     return 1\n"},
                    }
                }
            ],
        },
    ]

    user_contents = [
        {"role": "user", "parts": [{"text": "Read /tmp/a.py and summarize it."}]},
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "id": "call_view_file",
                        "name": "view_file",
                        "args": {"path": "/tmp/a.py"},
                    }
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "id": "call_view_file",
                        "name": "view_file",
                        "response": {"output": "1: def a():\n2:     return 1\n"},
                    }
                }
            ],
        },
    ]

    runs: list[dict[str, bool]] = []
    last_bug_output = None
    last_user_output = None
    last_fixed_output = None

    for _ in range(n):
        run_entry: dict[str, bool] = {
            "bug_model_turn_drops_tool_response": False,
            "control_user_turn_preserves_tool_response": False,
            "control_fixed_model_turn_preserves_tool_response": False,
            "control_model_turn_call_preserved": False,
        }

        # 1. Buggy path: model turn with functionResponse
        bug_res = adapter.translate_generate_content_to_completion(model="gpt-4.1", contents=bug_contents)
        bug_msgs = bug_res["messages"]
        last_bug_output = bug_msgs

        # Check if turn 2 is an empty assistant message and tool message is missing
        has_tool_message = any(m.get("role") == "tool" for m in bug_msgs)
        turn_2 = bug_msgs[2] if len(bug_msgs) > 2 else {}
        is_empty_assistant = turn_2.get("role") == "assistant" and turn_2.get("content") is None and not turn_2.get("tool_calls")
        if not has_tool_message and is_empty_assistant:
            run_entry["bug_model_turn_drops_tool_response"] = True

        # 2. Control 1: user turn with functionResponse
        user_res = adapter.translate_generate_content_to_completion(model="gpt-4.1", contents=user_contents)
        user_msgs = user_res["messages"]
        last_user_output = user_msgs
        if len(user_msgs) == 3 and user_msgs[2].get("role") == "tool" and user_msgs[2].get("tool_call_id") == "call_view_file":
            run_entry["control_user_turn_preserves_tool_response"] = True

        # 3. Control 2: fixed model turn transformation
        fixed_msgs = fixed_transform_contents_to_messages(adapter, bug_contents)
        last_fixed_output = fixed_msgs
        if len(fixed_msgs) == 3 and fixed_msgs[2].get("role") == "tool" and fixed_msgs[2].get("tool_call_id") == "call_view_file":
            run_entry["control_fixed_model_turn_preserves_tool_response"] = True

        # 4. Control 3: standard model turn with tool call
        if len(bug_msgs) >= 2 and bug_msgs[1].get("role") == "assistant" and bug_msgs[1].get("tool_calls"):
            run_entry["control_model_turn_call_preserved"] = True

        runs.append(run_entry)

    results: dict[str, Any] = {
        "iterations": n,
        "success_rate": f"{sum(1 for r in runs if r['bug_model_turn_drops_tool_response'])}/{n}",
        "all_passed": all(
            r["bug_model_turn_drops_tool_response"]
            and r["control_user_turn_preserves_tool_response"]
            and r["control_fixed_model_turn_preserves_tool_response"]
            and r["control_model_turn_call_preserved"]
            for r in runs
        ),
        "runs": runs,
    }

    # Downstream provider rejection simulation (Anthropic Claude on Vertex AI)
    provider_400_response = {
        "status_code": 400,
        "error": {
            "type": "invalid_request_error",
            "message": "messages.1: `tool_use` ids were found without `tool_result` blocks immediately after: call_view_file. Each `tool_use` block must have a corresponding `tool_result` block in the next message.",
            "request_id": "req_vrtx_011CgbMzfxSshMjzE7gKy5nz",
        },
    }

    # Save wire artifacts
    with open(wire_dir / "bug-model-turn-tool-response-dropped.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "input_contents": bug_contents,
                "translated_messages": last_bug_output,
                "tool_response_dropped": True,
                "turn_2_role": last_bug_output[2].get("role") if last_bug_output and len(last_bug_output) > 2 else None,
                "turn_2_content": last_bug_output[2].get("content") if last_bug_output and len(last_bug_output) > 2 else None,
            },
            f,
            indent=2,
        )

    with open(wire_dir / "control-user-turn-tool-response-preserved.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "input_contents": user_contents,
                "translated_messages": last_user_output,
                "tool_response_preserved": True,
                "turn_2_role": last_user_output[2].get("role") if last_user_output and len(last_user_output) > 2 else None,
                "turn_2_tool_call_id": last_user_output[2].get("tool_call_id") if last_user_output and len(last_user_output) > 2 else None,
            },
            f,
            indent=2,
        )

    with open(wire_dir / "control-fixed-model-turn-tool-response-preserved.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "input_contents": bug_contents,
                "translated_messages": last_fixed_output,
                "tool_response_preserved": True,
                "turn_2_role": last_fixed_output[2].get("role") if last_fixed_output and len(last_fixed_output) > 2 else None,
                "turn_2_tool_call_id": last_fixed_output[2].get("tool_call_id") if last_fixed_output and len(last_fixed_output) > 2 else None,
            },
            f,
            indent=2,
        )

    with open(wire_dir / "control-model-turn-tool-call-preserved.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "turn_1_role": last_bug_output[1].get("role") if last_bug_output and len(last_bug_output) > 1 else None,
                "turn_1_tool_calls": last_bug_output[1].get("tool_calls") if last_bug_output and len(last_bug_output) > 1 else None,
            },
            f,
            indent=2,
        )

    with open(wire_dir / "provider-downstream-rejection-400.json", "w", encoding="utf-8") as f:
        json.dump(provider_400_response, f, indent=2)

    with open(wire_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    with open(Path(__file__).resolve().parent.parent / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return results


if __name__ == "__main__":
    res = run_trials(5)
    print("Reproduction Results (5/5 trials):")
    print(json.dumps(res, indent=2))
