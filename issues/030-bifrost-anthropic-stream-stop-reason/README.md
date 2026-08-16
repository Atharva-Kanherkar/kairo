# 030, Bifrost `/anthropic/v1/messages` streaming ends a tool-call turn as `end_turn`

- **Upstream**: [bifrost#6123](https://github.com/maximhq/bifrost/issues/6123) (open).
  Reporter saw it on 1.6.6 and 1.6.9. The same shape was filed as
  [#3638](https://github.com/maximhq/bifrost/issues/3638) and fixed by #3640 in
  v1.5.4, so this is a **regression of an already-fixed bug** — the strongest
  possible argument for a permanent conformance suite.
- **Tool under test**: Bifrost gateway **v1.6.11** (`GET /api/version`), run via
  `npx -y @maximhq/bifrost`. Third tool in this repo, after LiteLLM and Switchyard.
- **Reproduced**: 2026-08-16. Offline, mock OpenAI-compatible upstream
  (`transcripts/030/mock_upstream.py`), no provider keys involved.
  **5/5 deterministic**, with three control cells 5/5 conformant.

## What breaks

A client calls the Anthropic-compatible route with `"stream": true`. The model turn
contains one sentence of text and then a `get_time` tool call. Bifrost serializes the
`tool_use` content block correctly — `content_block_start` type `tool_use`, the
`input_json_delta`, the `content_block_stop` — and then terminates the stream with:

```
event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":""}, ...}
```

`end_turn` is the Anthropic protocol's "the assistant is done talking". An agentic
client reads it as turn-complete, never executes the tool, and ends the exchange —
while the tool call it was supposed to run is sitting in the same stream it just
finished reading. The reporter's case was a nightly pipeline that silently produced
nothing for four days.

Pure tool-call turns (no leading text) are reported unaffected, which is what makes
this intermittent and hard to attribute from the client side.

## Wire evidence

`transcripts/030/`:

- `anthropic-stream.sse` — the violation. Contains both a `text` and a `tool_use`
  content block, terminal `stop_reason: "end_turn"`.
- `anthropic-nonstream.json` — same turn, same upstream, non-streaming:
  `content: [text, tool_use]`, `stop_reason: "tool_use"`. **Conformant.**
- `openai-stream.sse` — same turn on the OpenAI-shaped streaming route:
  `finish_reason: "tool_calls"`. **Conformant.**
- `results.json` — the 5-iteration matrix.
- `mock_upstream.py`, `config.json`, `hunt.py` — the rig.

### Control matrix (5 iterations each, same upstream turn)

| Route | Mode | Expected | Observed | |
|---|---|---|---|---|
| `/v1/chat/completions` | non-stream | `finish_reason: tool_calls` | `tool_calls` 5/5 | ✅ |
| `/v1/chat/completions` | stream | `finish_reason: tool_calls` | `tool_calls` 5/5 | ✅ |
| `/anthropic/v1/messages` | non-stream | `stop_reason: tool_use` | `tool_use` 5/5 | ✅ |
| `/anthropic/v1/messages` | **stream** | `stop_reason: tool_use` | **`end_turn` 0/5** | ❌ |

Three conformant controls on the same gateway, same upstream, same turn. Only the
Anthropic **streaming** path loses the reason, which isolates the defect to the
streaming serializer rather than to the translation as a whole or to the upstream.

## Divergence from the upstream ticket (read this before filing)

The reporter's upstream was an OpenAI **chat completions** provider reporting
`finish_reason: tool_calls`. On v1.6.11, the `/anthropic/v1/messages` route drives an
OpenAI-compatible upstream through the **Responses API** (`POST /v1/responses`), and it
still did so with `client.compat.convert_chat_to_responses: false` in `config.json`.
The mock therefore serves both dialects, and the reproduction above went through the
Responses upstream.

That difference is load-bearing in two directions:

- It **strengthens** the report: the defect is not specific to the chat-completions
  upstream shape. The terminal frame is wrong on the Responses upstream path too, so
  the fault is in the canonical → Anthropic streaming serialization, not in one
  upstream parser.
- It is **not** the reporter's exact configuration. We could not force the Anthropic
  route onto a chat-completions upstream on v1.6.11 to confirm that specific cell.

## Root cause

Not pinned to a line. The evidence localizes it to the canonical → Anthropic
**streaming** serializer that emits the terminal `message_delta`: the non-streaming
converter maps the same canonical turn to `tool_use` correctly, so the canonical
finish reason survives translation and is lost (or never consulted) when the terminal
streaming frame is built. Source audit of `core/providers/anthropic/` was not
performed for this writeup; the claim above rests on the behavioural isolation, not on
reading the mapping code.

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| The symptom is real on v1.6.11 | **High** | 5/5 deterministic, our own captured bytes |
| It is a streaming-only defect | **High** | 3 control cells conformant 5/5 on the same turn |
| It is the same defect class as #6123 | **High** | Identical route, mode, and terminal value |
| It reproduces on the reporter's exact upstream (chat completions) | **Unverified** | Could not force that path on v1.6.11 |
| Named source location | **Not established** | Behavioural isolation only, no source audit |
| Live-provider behaviour (real Anthropic or real OpenAI upstream) | **Untested** | Offline mock only; no keys used |

Single machine, single gateway version, single day. Everything above is what our
bytes show, not what the project's code says.

## How real the bug is

Real, and user-visible without instrumentation. The terminal frame is part of the
Anthropic wire contract, not an internal detail: `stop_reason` is the field an agent
loop switches on to decide whether to execute tools and continue. Getting it wrong
does not raise an error, log a warning, or fail a health check — the stream is
well-formed, the tool block is present and valid, and the client simply stops. That is
the worst failure profile for a gateway: silent, protocol-legal, and attributed by the
user to the model rather than to the proxy.

Severity is bounded by two things worth stating plainly. Only the streaming Anthropic
route is affected — the other three cells are correct, so a deployment not using that
route is unaffected. And a pure tool-call turn with no leading text reportedly
survives, so the failure is intermittent rather than total. That intermittency is
itself a cost: it makes the bug read as model flakiness.

## Test

`crates/harness/tests/conformance.rs`:

- `bifrost_anthropic_stream_loses_toolcall_stop_reason` — the frozen violation.
- `bifrost_anthropic_nonstream_control_keeps_toolcall_stop_reason` — the control that
  isolates it to streaming.
- `bifrost_openai_stream_control_keeps_toolcall_finish_reason` — the control that
  proves the upstream really did report a tool call.

The streaming assertion uses `anthropic_toolcall_stop_reason`, **the checker written
for bug 001 against LiteLLM 1.82**, unchanged. The invariant is a property of the
Anthropic wire contract, not of any one gateway, so it ported to a different proxy in a
different language with no edit. `anthropic_response_toolcall_stop_reason` is added
here as its non-streaming sibling.

Invariant, stated as a property rather than a bug: *if an Anthropic Messages response
carries a `tool_use` content block, its terminal `stop_reason` is `tool_use` — in both
streaming and non-streaming form.*

## Reproducing

```bash
cd transcripts/030
python3 mock_upstream.py &
npx -y @maximhq/bifrost -app-dir . -port 8080 &
python3 hunt.py
```
