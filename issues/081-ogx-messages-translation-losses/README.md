# 081, OGX /v1/messages translation mode: five silent losses

- **Upstream**: no ticket, not yet filed. Searched `ogx-ai/ogx` issues for
  "thinking", "adaptive", "content_filter", "refusal", "is_error", and
  "stop_reason" on 2026-09-22. Closest hits are unrelated surfaces:
  #6419 (Gemini tool thought signatures in the Vertex provider), #5521
  (missing `stop_sequence` schema field), #4892 (chat response schema
  alignment). None covers the `/v1/messages` translation path. The OGX
  conformance report (`docs/api-anthropic-messages/conformance`) is a
  schema-shape diff only and documents none of the five behaviors.
- **Tool under test**: OGX (ogx-ai/ogx) **v1.4.0**, commit `051a8a0`
  (`chore: prepare release v1.4.0`, released 2026-09-11), the current
  release at reproduction time.
- **Method**: OGX server run locally (`uv run ogx go --insecure --no-auth
  --port 8321`) with `OPENAI_BASE_URL` pointed at a deterministic capture
  upstream (`transcripts/081/capture_upstream.py`) that records every
  forwarded request body unmutated and returns canned OpenAI responses.
  Fully offline, deterministic, no keys, 5/5 trials per case.
- **Reproduced**: 2026-09-22. Evidence: `transcripts/081/`.
- **Label**: bug.
- **Not a credential incident**: no keys in the frozen files.

## What breaks

OGX advertises an Anthropic Messages compatibility layer at `/v1/messages`
for teams pointing the Anthropic SDK (Claude Code, Codex, OpenCode) at any
model. For providers that do not natively speak Anthropic, the adapter
translates Anthropic requests to OpenAI chat completions
(`src/ogx/providers/utils/inference/anthropic_translation.py`). Five
Anthropic dialect features are silently dropped on that path. All five
return HTTP 200 with no diagnostic. The agent loop on the other side of
`/v1/messages` reads the stop reason, the tool result error flag, and the
thinking history; each loss halts, misleads, or blinds it.

## Loss 1, adaptive thinking config silently ignored

A request with `thinking: {"type": "adaptive"}` returns 200 and the
forwarded OpenAI body carries no thinking or reasoning configuration of any
kind. The client believes adaptive thinking is active; the model runs
without it.

The inconsistency is the proof of intent: the same function refuses
`thinking: {"type": "enabled"}` with a 400 (`anthropic_request_to_openai`
raises `ValueError` unless `thinking.type == "enabled"` is the only
supported value). Translation mode therefore has a fail-closed rule for
thinking configs it cannot carry, and `adaptive`, which is part of the same
model union (`AnthropicThinkingConfig`, models.py:240), slips past it.
OGX's own docs claim "thinking are mapped to their OpenAI equivalents" on
the Anthropic Messages page, which is false for both values on this path.

- Evidence: `transcripts/081/ogx-adaptive-thinking-cases.json` (5/5,
  `client_status` 200, forwarded body has no `thinking`/`reasoning` key).
- Control: `transcripts/081/ogx-enabled-thinking-control-cases.json` (5/5,
  400, `invalid_request_error`), and the same capture pipeline forwards
  `stop`, `stop_sequences`, `tool_choice`, and `top_k` faithfully.

## Loss 2, assistant history thinking blocks and signatures dropped

Assistant history containing `thinking` blocks (with `signature`) is
accepted, returns 200, and the forwarded OpenAI assistant message contains
only the visible text. The thinking text and the signature both vanish.
The model loses the reasoning context it is supposed to see on the next
turn, and a client that echoes thinking blocks back (as the Anthropic spec
requires for multi-turn extended thinking) silently loses the signature it
would need for a native Anthropic provider.

Same class as kairo 016 (Switchyard, LiteLLM), 033 (Bifrost), 057 (any-llm).

- Evidence: `transcripts/081/ogx-thinking-history-cases.json` (5/5,
  forwarded body has neither the thinking text nor `SIG_AB12`).
- Control: the visible text in the same assistant turn survives
  (`"The answer is 42."` is forwarded).

## Loss 3, upstream `content_filter` finish translated to `end_turn`

Upstream `finish_reason: "content_filter"` becomes Anthropic
`stop_reason: "end_turn"` with empty content, on both the non-streaming and
streaming path. The Anthropic client cannot distinguish "model finished
naturally" from "upstream blocked the response". Same class as kairo 010
(Switchyard, cited upstream as Switchyard#369) and 034/035/036 (Bifrost).

- Evidence: `transcripts/081/ogx-contentfilter-cases.json` (5/5,
  `stop_reason` = `end_turn`) and
  `transcripts/081/ogx-contentfilter-stream-cases.json` (5/5, stream
  `message_delta` carries `end_turn`).
- Controls: `finish_reason: "length"` maps to `max_tokens` and
  `finish_reason: "stop"` to `end_turn` on the same surface
  (`canned/length.json`, frozen in the contentfilter cases baseline), so the
  mapping table works where OGX has a mapping; `content_filter` has none and
  falls through to `end_turn` (`_FINISH_TO_STOP_REASON`,
  anthropic_translation.py:76).

## Loss 4, upstream refusal text erased

An upstream chat completion with `refusal: "I cannot help with that
request."` and `content: null` reaches the Anthropic client as
`content: []` and `stop_reason: "end_turn"`. The refusal string exists in
the OpenAI dialect (`ogx_api/inference/models.py:669`) and is never read.
The client sees a silent empty reply instead of a refusal; the agent loop
cannot tell the model declined. Same class as kairo 036, 067, and 069.

- Evidence: `transcripts/081/ogx-refusal-cases.json` (5/5, no refusal text
  anywhere in the client response) and
  `transcripts/081/ogx-refusal-stream-cases.json` (5/5, zero content blocks
  in the stream).
- Control: a plain upstream text answer survives as a text block on the
  same path (frozen in `ogx-adaptive-thinking-cases.json`).

## Loss 5, `is_error` dropped from tool results

An Anthropic `tool_result` with `is_error: true` becomes a plain OpenAI
`role:"tool"` message. The forwarded body has no error marker anywhere.
The model reads "Error: permission denied" as if it were a normal result
and cannot reliably retry or escalate. Same class as kairo 006 loss 1 and
059 (any-llm).

- Evidence: `transcripts/081/ogx-is-error-cases.json` (5/5, forwarded tool
  message is `{"role":"tool","tool_call_id":"toolu_01ABC","content":"Error:
  permission denied"}`).
- Control: the tool result text itself is forwarded, and the same request
  without `is_error` translates identically, so only the flag is lost.

## Root cause

All five are in `src/ogx/providers/utils/inference/anthropic_translation.py`:

1. `anthropic_request_to_openai` (line 234) only rejects
   `thinking.type == "enabled"`; `adaptive` falls through with no mapping.
2. `convert_assistant_message` (line 172) handles `AnthropicTextBlock` and
   `AnthropicToolUseBlock` only; thinking and redacted thinking blocks in
   assistant history are dropped.
3. `_FINISH_TO_STOP_REASON` (line 76) has no entry for `content_filter`;
   `.get(finish_reason, "end_turn")` erases it.
4. `openai_response_to_anthropic` (line 268) reads `message.content` only;
   the `refusal` field is never mapped.
5. `convert_single_message` (line 109) copies tool result text and images
   only; `is_error` is never carried.

## Bug-or-not checklist

- Is the expected behavior the spec? The Anthropic Messages API contract is
  the spec: thinking configs configure the model, thinking blocks and
  signatures must be echoed on replay, refusal is a distinct stop reason and
  content, and `is_error` marks failed tool calls. OGX accepts all of these
  fields in its request models (they validate and return 200), which shows
  they are supported usage, not exotic input.
- Have maintainers ruled on it? No issue, PR, or comment classifies any of
  the five as intended. The opposite is true for Loss 1: OGX's own
  `ValueError` for `enabled` thinking shows the intended rule is to refuse
  unsupported thinking configs rather than drop them.
- Is the trigger supported usage? Yes: `--setup gpt` style stacks with
  OpenAI-compatible upstreams are a documented, recommended OGX setup, and
  Claude Code pointing at OGX is a first-class flow in the docs.
- Is a real boundary crossed? The Anthropic SDK client is entitled to the
  fields it sends and the terminal semantics it parses; the translated
  request silently omits what the client set, and the client-visible
  response silently mislabels the terminal reason.
- What fix would a maintainer ship? Map adaptive thinking to the closest
  OpenAI reasoning config or refuse it like `enabled`; encode thinking
  history in the target dialect or refuse; map `content_filter` to the
  Anthropic `refusal` stop reason; map upstream `refusal` to a text block
  with `stop_reason: refusal`; and carry `is_error` as an error marker in
  the tool content or refuse. All five are behavior changes, not docstring
  edits.

## Test

`ogx_adaptive_thinking_is_silently_ignored`,
`ogx_enabled_thinking_control_fails_closed`, `ogx_drops_thinking_history`,
`ogx_translates_content_filter_to_end_turn`,
`ogx_positive_stop_reason_controls_are_conformant`, `ogx_erases_refusal_text`,
`ogx_refusal_control_keeps_plain_content`, `ogx_drops_is_error_on_tool_result`
in `crates/harness/tests/conformance.rs`, all over frozen 5/5 captures in
`transcripts/081/`.

## Repro

```
# one time
git clone --branch v1.4.0 https://github.com/ogx-ai/ogx ~/kairo-targets/ogx
cd ~/kairo-targets/ogx && uv sync
OPENAI_BASE_URL=http://127.0.0.1:9101/v1 OPENAI_API_KEY=fake-key \
  uv run ogx go --insecure --no-auth --port 8321
# freeze + refreeze
python3 transcripts/081/hunt.py
```
