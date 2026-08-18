# 058, any-llm drops `disable_parallel_tool_use` on the Messages bridge

- **Upstream**: related to closed [any-llm#646](https://github.com/mozilla-ai/any-llm/issues/646)
  (`tool_choice` mistranslation). Same class as kairo 017, 031, 043.
- **Tool under test**: mozilla-ai/any-llm **1.26.0** (`any-llm-sdk`).
- **Blast radius**: Otari `/v1/messages` on non-Anthropic backends (see 057).
- **Reproduced**: 2026-08-18. Capture 5/5. OpenAI `completion()` control 5/5.
  Evidence: `transcripts/057/`.
- **Not a credential incident**: no keys in the frozen files.

## What breaks

A caller sends `tool_choice: {type: auto, disable_parallel_tool_use: true}`.
Sequential agent loops depend on that flag. any-llm forwards bare
`tool_choice: "auto"` with no `parallel_tool_calls: false`. Silent loss.

## Wire evidence

Violation — `transcripts/057/al-parallel-upstream.jsonl` (5/5):

```json
{"tool_choice":"auto","tools":[...],"messages":[...], ...}
```

Control — `transcripts/057/al-completion-control-upstream.jsonl` (5/5):

```json
{"parallel_tool_calls":false,"stop":["STOPPROBE"], ...}
```

Offline mock only: a real OpenAI backend rejects `parallel_tool_calls` without
tools. The control still proves the SDK can express the flag on the native
OpenAI path; only the Messages ingress drops the Anthropic form.

## Root cause

`any_llm/utils/messages_compat.py`, `_convert_tool_choice_to_openai`: reads
`type` only; `disable_parallel_tool_use` is never mapped to
`parallel_tool_calls: false`.

## Test

`any_llm_drops_disable_parallel_tool_use` (violation) and
`any_llm_completion_keeps_parallel_tool_calls` (control), both using
`parallel_tool_disable_preserved`.

## Repro

See `transcripts/057/hunt.py` (`parallel` and `ctrl_completion` cases).
