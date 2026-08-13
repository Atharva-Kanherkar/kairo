# 017, `disable_parallel_tool_use` is silently dropped

- **Upstream**: no ticket. Cross-format tool-choice field with no equivalent
  mapped on the wire.
- **Tools under test**: Switchyard 0.2.0; LiteLLM 1.96.2.
- **Method**: offline capture rig. No keys.
- **Reproduced**: 2026-08-13. Evidence: `transcripts/016/cap-parallel.jsonl`
  (Switchyard), `transcripts/016/cap-litellm-parallel.jsonl` (LiteLLM).
  Control: `transcripts/016/cap-litellm-openai-strict.jsonl` (LiteLLM
  same-format OpenAI chat keeps `parallel_tool_calls: false`).

## What breaks

An Anthropic request with

```
"tool_choice": {"type": "auto", "disable_parallel_tool_use": true}
```

is forwarded as `tool_choice: "auto"` with no `parallel_tool_calls: false`
and no `disable_parallel_tool_use`. The client asked for one tool at a time.
The backend is free to emit parallel calls. Silent, HTTP 200.

Switchyard's `ToolChoice` enum has Auto/Required/None/Tool/Raw and the
`{type: auto, ...}` object maps to `Auto`, discarding sibling fields.
LiteLLM's Anthropic-to-Responses adapter emits `"tool_choice": "auto"` and
drops the flag the same way.

Same-format OpenAI chat through LiteLLM (`parallel_tool_calls: false` on
`/v1/chat/completions`) is preserved. The loss is the translation, not the
proxy.

## Why it matters

Agents that disable parallel tools do it because the tools have side
effects or ordering constraints (apply_patch then run tests). A translator
that re-enables parallel calls makes those loops race. The model gets
blamed.

## Test invariants

1. Inbound `disable_parallel_tool_use: true` MUST survive as that flag or as
   `parallel_tool_calls: false` on OpenAI-shaped backends.
2. A dropped parallel-tool constraint MUST be reported, not discarded.

## Repro

```
python tools/capture_server.py $PWD/transcripts/016/cap-parallel.jsonl &
tools/switchyard/target/release/switchyard-server --config tools/switchyard-capture.toml --port 9000 &
curl -s localhost:9000/v1/messages -H 'anthropic-version: 2023-06-01' \
  -d @transcripts/016/req-parallel.json
# forwarded tool_choice is the string "auto"; parallel_tool_calls absent
```
