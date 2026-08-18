# 059, any-llm drops `is_error` on tool results in the Messages bridge

- **Upstream**: no ticket, not yet filed. Same class as kairo 006 (Switchyard,
  LiteLLM via Responses).
- **Tool under test**: mozilla-ai/any-llm **1.26.0** (`any-llm-sdk`).
- **Reproduced**: 2026-08-18. Capture 5/5. Evidence: `transcripts/057/`.
- **Not a credential incident**: no keys in the frozen files.

## What breaks

When a tool fails, Anthropic clients mark the result with `is_error: true`.
The model uses that to retry or explain the failure. any-llm flattens the
tool result to a plain string (`"permission denied"`) with no error marker.

## Wire evidence

`transcripts/057/al-is-error-upstream.jsonl` (5/5):

```json
{"role":"tool","tool_call_id":"toolu_1","content":"permission denied"}
```

No `is_error`, no `status`, no equivalent field anywhere in the body.

## Root cause

`any_llm/utils/messages_compat.py`, `_convert_user_blocks_to_openai`: the
`tool_result` branch copies text content only and ignores `is_error`.

## Test

`any_llm_drops_is_error_on_tool_result` using `is_error_forwarded`.

## Repro

See `transcripts/057/hunt.py` (`is_error` case).
