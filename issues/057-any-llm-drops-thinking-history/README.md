# 057, any-llm drops assistant `thinking` blocks from replayed history

- **Upstream**: no ticket, not yet filed. Same class as kairo 016 (Switchyard,
  LiteLLM leak) and 033 (Bifrost drop). Root cause is in
  `any_llm/utils/messages_compat.py` (`_convert_assistant_blocks_to_openai`
  handles `text` and `tool_use` only).
- **Tool under test**: mozilla-ai/any-llm **1.26.0** (`any-llm-sdk` on PyPI).
  Messages API bridged to an OpenAI-compatible backend via `provider="openai"`
  and `api_base` pointed at a capture mock. Otari routes through this SDK.
- **Reproduced**: 2026-08-18. Capture 5/5. Evidence: `transcripts/057/`.
- **Not a credential incident**: no keys in the frozen files.

## What breaks

Multi-turn agent loops that replay Anthropic history with extended thinking
send assistant turns containing `thinking` blocks plus a signature. The model
needs that block (or its signature) on the next turn. any-llm's Messages
bridge forwards only the visible `"4"` text; `THINKPROBE` and the signature
vanish. HTTP 200, no warning.

## Wire evidence

`transcripts/057/al-thinking-history-upstream.jsonl` — forwarded body (5/5):

```json
{"messages":[{"role":"user","content":"2+2?"},{"role":"assistant","content":"4"},{"role":"user","content":"now 3+3"}], ...}
```

The thinking block and signature are absent. Visible assistant text survives.

## Root cause

`any_llm/utils/messages_compat.py`, `_convert_assistant_blocks_to_openai`:
the loop over content blocks ignores `type: thinking`.

## Test

`any_llm_drops_thinking_history` using `thinking_text_forwarded` with probe
`THINKPROBE`. Control: `thinking_not_leaked_as_visible_text` on the same
fixture (dropped, not leaked).

Invariant: *private thinking text from replayed history appears somewhere in
the request the bridge forwards upstream.*

## Repro

```
python3 -m venv /tmp/kairo-venv && /tmp/kairo-venv/bin/pip install 'any-llm-sdk[openai]'
python3 tools/mock_upstream.py 9996 /tmp/cap.jsonl transcripts/057/canned-ok.json &
python3 transcripts/057/hunt.py
# or run the thinking_history case only from hunt.py
```
