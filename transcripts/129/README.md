# Transcript 129: LiteLLM Google GenAI Adapter Drops Tool Results in Model Turns

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's Google GenAI adapter drops tool execution responses (`functionResponse` and `function_response` parts) when they arrive in `role: "model"` turns during `generateContent` translation to chat completion messages (Upstream PR [BerriAI/litellm#49552](https://github.com/BerriAI/litellm/pull/49552), related to #46308).

In `litellm/google_genai/adapters/transformation.py`, `_transform_contents_to_messages` handles `role == "user"` by extracting `functionResponse` parts and appending them as `ChatCompletionToolMessage` objects:
```python
elif "functionResponse" in part:
    func_response = part["functionResponse"]
    tool_message = ChatCompletionToolMessage(
        role="tool",
        tool_call_id=f"call_{func_response.get('name', 'unknown')}",
        content=json.dumps(func_response.get("response", {})),
    )
    tool_messages.append(tool_message)
```

However, in `role == "model"` turns:
```python
elif role == "model":
    combined_text = ""
    tool_calls: list[ChatCompletionAssistantToolCall] = []

    for part in parts:
        if isinstance(part, dict):
            if "text" in part:
                combined_text += part["text"]
            elif "functionCall" in part:
                func_call = part["functionCall"]
                tool_call = ...
                tool_calls.append(tool_call)
```

The adapter only checks for `text` and `functionCall`. When an agent loop (such as Google Antigravity CLI, or standard Gemini agent harnesses) submits a tool response in a `model` turn (or session history records function responses under `role: "model"`), `combined_text` remains empty and `tool_calls` remains empty. The adapter emits an empty assistant message:
```python
ChatCompletionAssistantMessage(role="assistant", content=None)
```
and drops the tool execution response entirely.

When forwarded to downstream chat completion providers such as Anthropic Claude (e.g. Claude on Vertex AI) or OpenAI:
- The provider receives an assistant turn invoking a tool call, followed immediately by an empty assistant turn with `content: null`.
- The provider rejects the request with HTTP 400 (`messages.1: tool_use ids were found without tool_result blocks immediately after: call_view_file`).
- The multi-turn agent conversation crashes on turn 2.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49552](https://github.com/BerriAI/litellm/pull/49552)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case (`role: "model"` with `functionResponse`)**:
   - Translate a `generateContent` conversation where turn 2 is `role: "model"` containing `functionResponse` for `view_file`.
   - Result: Turn 2 is emitted as `{'role': 'assistant', 'content': None}`, omitting `role: "tool"`. The tool call remains unanswered.
2. **Control 1 (`role: "user"` with `functionResponse`)**:
   - Translate the same conversation with `role: "user"` for turn 2.
   - Result: Turn 2 is correctly emitted as `{'role': 'tool', 'tool_call_id': 'call_view_file', 'content': '{"output": "1: def a():\\n2:     return 1\\n"}'}`.
3. **Control 2 (Fixed PR #49552 adapter transformation)**:
   - Apply the upstream PR #49552 logic extracting `functionResponse` and `function_response` in model turns.
   - Result: Turn 2 is correctly emitted as `{'role': 'tool', 'tool_call_id': 'call_view_file', 'content': '...'}`.
4. **Control 3 (Standard model turn with `functionCall`)**:
   - Turn 1 containing only `functionCall` is preserved as `{'role': 'assistant', 'tool_calls': [...]}`.

## Artifacts in `wire/`

- `bug-model-turn-tool-response-dropped.json`: Captured translation output showing turn 2 converted to an empty assistant message with `content: null`.
- `control-user-turn-tool-response-preserved.json`: Captured translation output for user turn showing tool message correctly emitted.
- `control-fixed-model-turn-tool-response-preserved.json`: Captured translation output using fixed PR #49552 logic.
- `control-model-turn-tool-call-preserved.json`: Captured translation output for turn 1 showing tool call preserved.
- `provider-downstream-rejection-400.json`: Simulated downstream provider rejection (HTTP 400 from Anthropic Claude / Vertex AI).
- `results.json`: 5 of 5 trials demonstrating 100% determinism.
