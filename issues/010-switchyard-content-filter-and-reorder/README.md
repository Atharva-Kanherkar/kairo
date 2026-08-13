# 010 - Switchyard: content_filter lost, and reasoning/text reordered within a chunk

Two of Switchyard's own open bugs, independently reproduced with the offline
capture rig. No keys.

- **Tool under test**: Switchyard `switchyard-server` 0.2.0 (source build, commit 2bef154).
- **Method**: mock upstream returns exact canned bytes; Switchyard translates
  OpenAI to Anthropic; we read the output.
- **Reproduced**: 2026-08-12. Evidence in `transcripts/015/`.

## Bug A - content_filter silently becomes end_turn (their #369)

Upstream OpenAI response with `finish_reason: "content_filter"`. Switchyard's
Anthropic output:

```
stop_reason: "end_turn"
```

The safety signal is erased. An Anthropic client cannot tell the turn was
filtered versus completed normally. Anthropic has no exact equivalent, but the
correct target is `refusal` (or surfacing the loss), not `end_turn`, which
means "the model finished naturally." Matches their open issue #369.

## Bug B - reasoning and text reordered within a chunk (their #242)

When one OpenAI chunk carries both `reasoning_content` and `content`,
Switchyard emits the text block before the thinking block regardless of the
order in the source chunk.

Upstream chunk (reasoning first): `reasoning_content:"THINK-FIRST "` then
`content:"TEXT-AFTER "`. Switchyard output order:

```
block_start idx 0 text      -> "TEXT-AFTER "
block_start idx 1 thinking  -> "THINK-FIRST "
```

The model reasoned, then spoke; the client is told it spoke, then reasoned. For
interleaved multi-step reasoning this scrambles the actual sequence. Confirmed
in both directions: text-first and reasoning-first source chunks both come out
text-first. Matches their open issue #242.

Note: when reasoning and text arrive in *separate* chunks, order is preserved
correctly (see `transcripts/015/resp-242.sse`). The reorder is specific to a
single chunk holding both.

## Test invariants

1. A backend `finish_reason` with no exact target equivalent
   (`content_filter`) must map to the closest safety signal or be surfaced,
   never silently to `end_turn`.
2. Within a stream, the relative order of reasoning and text as the backend
   emitted them must be preserved, including when both appear in one chunk.

## Repro

```
tools/litellm-env/bin/python tools/mock_upstream.py 9999 /tmp/cap.jsonl transcripts/015/canned-thinkfirst.sse &
tools/switchyard/target/release/switchyard-server --config tools/switchyard-capture.toml --port 9000 &
curl -sN localhost:9000/v1/messages -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"captured-model","max_tokens":100,"stream":true,"messages":[{"role":"user","content":"hi"}]}'
```
