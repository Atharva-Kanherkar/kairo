# 060, any-llm drops image bytes inside tool results

- **Upstream**: no ticket, not yet filed. Same class as kairo 007 (Switchyard
  stringifies, LiteLLM deletes). Related to open
  [any-llm#1295](https://github.com/mozilla-ai/any-llm/issues/1295) (Gemini
  inline_data loss on a different path).
- **Tool under test**: mozilla-ai/any-llm **1.26.0** (`any-llm-sdk`).
- **Reproduced**: 2026-08-18. Capture 5/5. Evidence: `transcripts/057/`.
- **Not a credential incident**: no PNG payload is secret; bytes are synthetic.

## What breaks

Browser and vision agents return screenshots inside `tool_result` blocks.
any-llm keeps the leading text (`"here it is:"`) and deletes the embedded
PNG. The upstream model never sees the image. HTTP 200.

## Wire evidence

`transcripts/057/al-toolresult-image-upstream.jsonl` (5/5):

```json
{"role":"tool","tool_call_id":"toolu_1","content":"here it is:"}
```

The base64 PNG probe (`iVBORw0KGgo...`) is absent. No `image_url` field.

## Root cause

`any_llm/utils/messages_compat.py`, `_convert_user_blocks_to_openai`: when
`tool_result.content` is a list, only `type: text` blocks are concatenated;
`type: image` blocks are skipped.

## Test

`any_llm_drops_image_in_tool_result` using `document_body_forwarded` with
the PNG probe prefix `iVBORw0KGgo`.

## Repro

See `transcripts/057/hunt.py` (`tool_result_image` case).
