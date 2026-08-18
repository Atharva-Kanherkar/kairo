# 060, any-llm drops image bytes inside tool results

- **Upstream**: no ticket, not yet filed. Same class as kairo 007 (Switchyard
  stringifies, LiteLLM deletes). Related to open
  [any-llm#1295](https://github.com/mozilla-ai/any-llm/issues/1295) (Gemini
  inline_data loss on a different path).
- **Tool under test**: mozilla-ai/any-llm **1.26.0** (`any-llm-sdk`).
- **Blast radius**: Otari `/v1/messages` on non-Anthropic backends (see 057).
- **Reproduced**: 2026-08-18. Capture 5/5. Evidence: `transcripts/057/`.
- **Not a credential incident**: synthetic PNG probe only.

## What breaks

Browser and vision agents return screenshots inside `tool_result` blocks.
any-llm keeps the leading text (`"here it is:"`) and deletes the embedded
PNG. The upstream model never sees the image. HTTP 200.

## Wire evidence

Violation — `transcripts/057/al-toolresult-image-upstream.jsonl` (5/5):

```json
{"role":"tool","tool_call_id":"toolu_1","content":"here it is:"}
```

The base64 PNG probe is absent. No `image_url` field on the tool message.

Control — `transcripts/057/al-user-image-upstream.jsonl` (5/5): the same PNG
in plain user content maps to `image_url` with the bytes intact. The loss is
specific to `tool_result`, not "any-llm can't do multimodal."

## Root cause

`any_llm/utils/messages_compat.py`, `_convert_user_blocks_to_openai`: when
`tool_result.content` is a list, only `type: text` blocks are concatenated;
`type: image` blocks are skipped.

## Test

`any_llm_drops_image_in_tool_result` (5/5, exact violation string, asserts
no `image_url`). Control: `any_llm_user_image_control_keeps_png_bytes`.

## Repro

```
/tmp/kairo-venv/bin/python3 transcripts/057/hunt.py
```
