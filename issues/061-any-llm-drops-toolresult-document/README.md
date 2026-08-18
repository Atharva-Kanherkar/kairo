# 061, any-llm drops document bytes inside tool results

- **Upstream**: no ticket, not yet filed. Same class as kairo 018 (user
  document dropped or dumped on gateways).
- **Tool under test**: mozilla-ai/any-llm **1.26.0** (`any-llm-sdk`).
- **Blast radius**: Otari `/v1/messages` on non-Anthropic backends (see 057).
- **Reproduced**: 2026-08-18. Capture 5/5. Evidence: `transcripts/057/`.
- **Not a credential incident**: synthetic `DOCBODY` probe only.

## What breaks

Agents that read files often return a `document` block inside a tool result
alongside summary text. any-llm forwards only `"result text"` and deletes
`DOCBODY`. The upstream model cannot see the attachment.

## Wire evidence

Violation — `transcripts/057/al-toolresult-document-upstream.jsonl` (5/5):

```json
{"role":"tool","tool_call_id":"toolu_1","content":"result text"}
```

Control — `transcripts/057/al-user-document-upstream.jsonl` (5/5): `DOCBODY`
survives on the user-content route (forwarded verbatim as an Anthropic-shaped
`document` block inside OpenAI content; likely a 400 on a real backend, a
third variant of the 018 class).

## Root cause

Same function as 060: `_convert_user_blocks_to_openai` concatenates text
blocks only inside list-shaped tool results; `type: document` is ignored.

## Test

`any_llm_drops_document_in_tool_result` (5/5). Control:
`any_llm_user_document_control_keeps_docbody`.

## Repro

```
/tmp/kairo-venv/bin/python3 transcripts/057/hunt.py
```
