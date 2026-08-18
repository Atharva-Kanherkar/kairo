# 057, any-llm drops assistant `thinking` blocks from replayed history

- **Upstream**: no ticket, not yet filed. Same class as kairo 016 (Switchyard,
  LiteLLM leak) and 033 (Bifrost drop). Root cause is in
  `any_llm/utils/messages_compat.py` (`_convert_assistant_blocks_to_openai`
  handles `text` and `tool_use` only).
- **Tool under test**: mozilla-ai/any-llm **1.26.0** (`any-llm-sdk` on PyPI).
  Messages API bridged to an OpenAI-compatible backend via `provider="openai"`
  and `api_base` pointed at a capture mock.
- **Blast radius**: Otari (`mozilla-ai/otari`, depends on `any-llm-sdk[all]>=1.24.0`)
  exposes `POST /v1/messages` for Claude Code. This bridge runs for **non-Anthropic
  backends only**; the native Anthropic provider overrides `_amessages` and never
  hits `messages_compat`.
- **Reproduced**: 2026-08-18. Capture 5/5. Evidence: `transcripts/057/`.
- **Not a credential incident**: no keys in the frozen files.

## What breaks

Multi-turn agent loops that replay Anthropic history with extended thinking
send assistant turns containing `thinking` blocks plus a signature. The model
needs that block (or its signature) on the next turn. any-llm's Messages
bridge forwards only the visible `"4"` text; `THINKPROBE` and the signature
vanish. HTTP 200, no warning.

The inconsistency is worse than a one-way drop: `chat_completion_to_message_response`
in the same file **decodes** upstream `msg.reasoning` into an Anthropic
`thinking` block for the client, but `_convert_assistant_blocks_to_openai` will
not re-encode that block on the next request. A client that reads thinking out
of any-llm's own Messages response cannot hand it back to any-llm.

## Wire evidence

`transcripts/057/al-thinking-history-upstream.jsonl` — forwarded body (5/5):

```json
{"messages":[{"role":"user","content":"2+2?"},{"role":"assistant","content":"4"},{"role":"user","content":"now 3+3"}], ...}
```

The thinking block and signature are absent. Visible assistant text survives.

## Root cause

`any_llm/utils/messages_compat.py`, `_convert_assistant_blocks_to_openai`:
the loop over content blocks ignores `type: thinking`. The same module maps
`params.thinking` (request config) onto `reasoning_effort` while dropping
thinking history.

## Test

`any_llm_drops_thinking_history` walks all five capture lines and requires
the exact `thinking_text_forwarded` violation string. Control:
`any_llm_thinking_is_dropped_not_leaked` on the same fixture.

## Repro

```
python3 -m venv /tmp/kairo-venv
/tmp/kairo-venv/bin/pip install 'any-llm-sdk[openai]'
/tmp/kairo-venv/bin/python3 transcripts/057/hunt.py
```

Do not start a separate mock on port 9996; the hunt spawns its own.
