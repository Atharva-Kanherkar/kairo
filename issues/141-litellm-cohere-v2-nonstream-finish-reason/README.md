# 141, LiteLLM Hardcodes Cohere v2 Non-Streaming Finish Reason to Stop

- **Upstream**: Open PR [BerriAI/litellm#49529](https://github.com/BerriAI/litellm/pull/49529) (`fix(cohere): map Cohere v2 finish_reason on non-streaming chat responses`), resolving Linear ticket LIT-9731
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-02, macOS, Python 3.12, module `litellm.llms.cohere.chat.v2_transformation`

## Bug or not

- **Is the expected behavior really the spec?** Yes. Under the OpenAI Chat Completions API specification, `choices[0].finish_reason` communicates the reason the model stopped generating. When tool calls are generated, it must be `"tool_calls"`. When output length limit is reached, it must be `"length"`. When generation completes naturally, it must be `"stop"`. Cohere v2 non-streaming API returns `finish_reason: "TOOL_CALL"`, `"MAX_TOKENS"`, `"COMPLETE"`, and `"STOP_SEQUENCE"`. LiteLLM must translate these reasons rather than defaulting to `"stop"`.
- **Have maintainers already ruled on it?** Yes. LiteLLM maintainer jesus-berri filed PR #49529 explicitly to fix this defect, noting that non-streaming Cohere v2 responses completely ignored Cohere's `finish_reason` and always returned `"stop"`.
- **Is the trigger supported usage?** Yes. Standard non-streaming completion calls (`stream=False`) to Cohere v2 models (`cohere_chat/v2/command-a-03-2025`, `cohere_chat/v2/command-r-plus`) with tools configured or max token limits.
- **Is a real boundary crossed?** Yes. Provider response translation boundary. LiteLLM erases the provider's `finish_reason` signal and hardcodes `"stop"`, corrupting the agent loop control flow.
- **What fix would a maintainer ship?** In `litellm/llms/cohere/chat/v2_transformation.py`, assign `model_response.choices[0].finish_reason = map_finish_reason(cohere_v2_chat_response.get("finish_reason", "COMPLETE"))`.
- **Classification**: `bug`

## What breaks

1. Premature agent loop termination: In agent runtimes (LangChain, AutoGen, CrewAI, LlamaIndex, OpenAI Agents SDK), the execution loop examines `response.choices[0].finish_reason == "tool_calls"` to decide whether to invoke the generated tools. Because unpatched LiteLLM reports `"stop"`, the agent loop treats the turn as completed and terminates without executing the tool calls.
2. Silent truncation: When a response is truncated by `max_tokens`, Cohere returns `finish_reason: "MAX_TOKENS"`. Unpatched LiteLLM reports `"stop"` instead of `"length"`, hiding truncation and misleading callers into assuming the response was fully generated.
3. Dialect inconsistency: Streaming Cohere v2 completions already translate `finish_reason` correctly (mapping `TOOL_CALL` to `tool_calls`), creating a behavioral discrepancy between streaming and non-streaming modes.

## Wire evidence

- `bug-cohere-v2-tool-call-finish-reason-stop.json`: Wire capture showing Cohere returning `finish_reason: "TOOL_CALL"` and tool calls, but LiteLLM emitting `choices[0].finish_reason == "stop"`.
- `bug-cohere-v2-max-tokens-finish-reason-stop.json`: Wire capture showing Cohere returning `finish_reason: "MAX_TOKENS"`, but LiteLLM emitting `choices[0].finish_reason == "stop"`.
- `control-fixed-cohere-v2-tool-calls.json`: Control capture showing PR #49529 patched response mapping `TOOL_CALL` to `tool_calls`.
- `control-fixed-cohere-v2-max-tokens.json`: Control capture showing PR #49529 patched response mapping `MAX_TOKENS` to `length`.
- `control-cohere-v2-complete.json`: Control capture showing Cohere `COMPLETE` properly mapping to `stop`.
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause

In `litellm/llms/cohere/chat/v2_transformation.py`:
In `CohereV2ChatConfig.transform_response()`, the method translated message content, citations, and tool call objects into `model_response.choices[0].message`, but never assigned `model_response.choices[0].finish_reason`. Because `Choices` defaults `finish_reason` to `"stop"`, every non-streaming Cohere v2 response returned `"stop"`, ignoring the provider's `finish_reason` field entirely.

PR #49529 resolves this by importing `map_finish_reason` and assigning:
```python
model_response.choices[0].finish_reason = map_finish_reason(
    cohere_v2_chat_response.get("finish_reason", "COMPLETE")
)
```

## Test

- Invariant checker: `cohere_v2_finish_reason_mapped` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_cohere_v2_tool_call_finish_reason_stop_reproduction`
  * `litellm_cohere_v2_max_tokens_finish_reason_stop_reproduction`
  * `litellm_fixed_cohere_v2_tool_calls_control`
  * `litellm_fixed_cohere_v2_max_tokens_control`
  * `litellm_cohere_v2_complete_control`
  * `litellm_cohere_v2_finish_reason_matrix_5_runs_reproduce_consistently`
