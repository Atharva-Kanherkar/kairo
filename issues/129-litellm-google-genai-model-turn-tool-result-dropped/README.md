# 129, LiteLLM Google GenAI Adapter Drops Tool Results in Model Turns

- **Upstream**: [BerriAI/litellm#49552](https://github.com/BerriAI/litellm/pull/49552), open as of 2026-11-01
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.google_genai.adapters.transformation`

## Bug or not

- **Is the expected behavior really the spec?** Yes. Both Google GenAI `generateContent` and OpenAI/Claude chat completion specifications support multi-turn conversations with tool calls and their results. When translating Gemini format requests to OpenAI or Claude formats, tool execution results must be mapped to `role: "tool"` messages that answer preceding tool calls, regardless of whether the agent formatted the response inside a `model` or `user` turn.
- **Have maintainers already ruled on it?** Yes. Acknowledged in PR #49552 (`fix(google_genai): emit tool messages for functionResponse parts in model turns`, related to #46308). Maintainers confirmed that agent tools such as Google Antigravity CLI record tool results in model turns and that dropping them breaks subsequent provider requests.
- **Is the trigger supported usage?** Yes. Calling LiteLLM's Google GenAI endpoint (`POST /v1beta/models/...:generateContent` or `GoogleGenAIAdapter().translate_generate_content_to_completion()`) with multi-turn conversation history containing tool calls and results is documented, standard usage.
- **Is a real boundary crossed?** Yes. Protocol and data integrity boundary. Tool results are erased from the conversation history and replaced with an empty assistant message (`role: "assistant", content: null`), causing downstream backends (Claude on Vertex AI, OpenAI) to reject subsequent requests with HTTP 400.
- **What fix would a maintainer ship?** Extract `functionResponse` and `function_response` parts in `model` turns and emit them as `tool` messages in `_transform_contents_to_messages` in `litellm/google_genai/adapters/transformation.py`.
- **Classification**: `bug`

## What breaks

When an autonomous agent (such as Google Antigravity CLI or Gemini-compatible agent harnesses) interacts with LiteLLM to route tasks to non-Gemini models (such as Claude on Vertex AI or OpenAI GPT-4o):
1. The user asks the agent to perform an action (e.g. read and summarize a file).
2. The agent issues a `generateContent` request and receives a `functionCall` for `view_file` in a `model` turn.
3. The agent executes `view_file` locally and sends the tool result inside a `functionResponse` part in a `model` turn on the next request.
4. LiteLLM's `GoogleGenAIAdapter._transform_contents_to_messages` inspects `role == "model"`, but only checks for `text` and `functionCall`.
5. The tool result is discarded. Because `combined_text` and `tool_calls` are empty, LiteLLM outputs `ChatCompletionAssistantMessage(role="assistant", content=None)` instead of a `tool` message.
6. Downstream providers (Anthropic Claude or OpenAI) receive an assistant message that called a tool, followed by an empty assistant message rather than the expected tool result message.
7. Claude on Vertex AI returns `400 Bad Request: messages.1: tool_use ids were found without tool_result blocks immediately after: call_view_file`.
8. The agent workflow crashes immediately on turn 2.

## Wire evidence

- `bug-model-turn-tool-response-dropped.json`: Captured translation output showing turn 2 converted to an empty assistant message (`content: null`) with no tool message.
- `control-user-turn-tool-response-preserved.json`: Captured translation output for user turn showing tool message correctly emitted with `tool_call_id: "call_view_file"`.
- `control-fixed-model-turn-tool-response-preserved.json`: Captured translation output using fixed PR #49552 logic showing tool message preserved for model turn.
- `control-model-turn-tool-call-preserved.json`: Captured translation output showing turn 1 tool call preserved.
- `provider-downstream-rejection-400.json`: Simulated downstream provider HTTP 400 rejection from Anthropic Claude on Vertex AI.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause (if found)

In `litellm/google_genai/adapters/transformation.py`, lines 565-605:
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
While `role == "user"` checks `elif "functionResponse" in part:` and extracts tool messages, `role == "model"` never checks `functionResponse` or `function_response`. As a result, model turns carrying tool results are dropped and converted to empty assistant messages.

## Test

- Invariant checker: `google_genai_model_turn_tool_results_preserved` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_google_genai_model_turn_tool_result_dropped_reproduction`
  * `litellm_google_genai_user_turn_tool_result_preserved_control`
  * `litellm_google_genai_fixed_model_turn_tool_result_preserved_control`
  * `litellm_google_genai_model_turn_tool_call_preserved_control`
  * `litellm_google_genai_matrix_5_runs_reproduce_consistently`
