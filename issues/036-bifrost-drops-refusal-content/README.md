# 036, Bifrost delivers an upstream refusal to Anthropic clients as empty content

- **Upstream**: no ticket found. Sibling of 034 and 035, and the most destructive of
  the three: here the mapping loses not just the stop reason but the entire message
  body.
- **Tool under test**: Bifrost gateway **v1.6.11**, `npx -y @maximhq/bifrost`.
- **Reproduced**: 2026-08-16, offline capture rig (`transcripts/bifrost-rig/`),
  no provider keys. **5/5**, with the control passing 5/5.

## What breaks

The upstream declines to answer and returns a refusal — on the Responses shape, a
content part of `type: "refusal"` carrying the explanation text. Through
`/anthropic/v1/messages`, the client receives:

```json
{"content": [], "stop_reason": "end_turn"}
```

An empty `content` array. Not the refusal text, not a placeholder, not an error —
a well-formed, successful HTTP 200 describing an assistant turn in which the model
said nothing at all.

Two distinct losses stack here. The refusal *reason* is erased (`end_turn`, as in
034 and 035), and the refusal *text* is erased with it. A caller cannot tell the
model refused, and cannot show the user why, because both halves of the message are
gone. The turn is indistinguishable from a model that returned silence.

## Wire evidence

`transcripts/036/anthropic-response.json` — what the client received:

```json
{"id":"resp_hunt","type":"message","role":"assistant","content":[],
 "model":"mimo-v2.5","stop_reason":"end_turn", ...}
```

`transcripts/036/control-plain-response.json` — **the control**: an ordinary turn
through the same route on the same gateway keeps its blocks
(`["text","tool_use"]`, 5/5). Empty content is not simply how this gateway answers;
it is what happens to a refusal specifically.

| Upstream returned | Anthropic client received | |
|---|---|---|
| refusal part with text | `content: []`, `stop_reason: end_turn` 5/5 | ❌ |
| ordinary text + tool call | `["text","tool_use"]` 5/5 | ✅ |

## Root cause

Not pinned to a line. Localized to the canonical → Anthropic response mapping of
content parts: a `refusal` part has no case in the block conversion, so it is
skipped, and because it was the turn's only content the array ends up empty. The
`stop_reason` falls back to `end_turn` as in 034 and 035.

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| The client receives empty content | **High** | Our own captured bytes, 5/5 |
| Not the gateway's normal behaviour | **High** | Plain-turn control keeps content, 5/5 |
| The refusal part is the trigger | **High** | Only the refusal scenario empties the array |
| A mixed turn (refusal + text) also loses the refusal | **Untested** | Only a refusal-only turn was probed |
| Named source location | **Not established** | Behavioural isolation only |

## How real the bug is

Real, and the most user-visible failure in this batch. The others degrade metadata;
this one deletes the message. An application relaying the assistant turn shows a
blank reply, and the user experiences the product as broken rather than as having
been refused. A pipeline that asserts on non-empty content raises an error whose
cause is nowhere in the payload.

It is bounded by requiring the upstream to emit a refusal part, which not every
provider does, and the untested mixed-content case may well be less severe — if
other blocks are present the array would not be empty, though the refusal text
would still be missing.

## Test

`bifrost_drops_refusal_content_entirely` (the frozen violation) and
`bifrost_plain_turn_keeps_content` (the control), using the new
`response_content_not_empty` checker.

Invariant: *a turn the upstream filled with content never reaches the client as an
empty content array.*

## Reproducing

```bash
cd transcripts/bifrost-rig
python3 capture_upstream.py &
npx -y @maximhq/bifrost -app-dir . -port 8080 &
python3 hunt.py
```
