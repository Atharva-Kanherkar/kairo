# 035, Bifrost reports a truncated turn to Anthropic clients as `end_turn`

- **Upstream**: no ticket found. Sibling of 034 and 036: the same
  everything-becomes-`end_turn` fallback, swallowing a different signal. Adjacent
  open ticket on the streaming route:
  [bifrost#6081](https://github.com/maximhq/bifrost/issues/6081)
  (Anthropic-compatible stream never sends `message_delta`/`message_stop` when the
  completion is truncated by `max_tokens`).
- **Tool under test**: Bifrost gateway **v1.6.11**, `npx -y @maximhq/bifrost`.
- **Reproduced**: 2026-08-16, offline capture rig (`transcripts/bifrost-rig/`),
  no provider keys. **5/5**.

## What breaks

The upstream stops generating because it hit the output-token ceiling and reports
it (`finish_reason: "length"` on the chat shape; `status: "incomplete"` with
`incomplete_details.reason: "max_output_tokens"` on the Responses shape). Through
`/anthropic/v1/messages`, the client is told `stop_reason: "end_turn"`.

Anthropic has a dedicated value for this: `max_tokens`. Reporting `end_turn`
asserts the opposite of what happened — the model did not finish, it was cut off
mid-sentence.

The consequence is specific: **truncation is the one stop reason a caller is
supposed to act on automatically.** A client that sees `max_tokens` continues the
generation, raises the budget, or tells the user the answer is partial. A client
that sees `end_turn` treats a severed answer as the whole answer. Nothing in the
payload distinguishes the two — a truncated turn and a complete one are byte-shaped
the same apart from this field.

## Wire evidence

Both halves of the exchange are frozen, and both are needed: `end_turn` is also the
ordinary success value, so the client body alone cannot distinguish a truncated turn
from a finished one. Only the upstream payload makes "truncated" observable.

`transcripts/035/upstream-response.json` — what the upstream returned:

```json
{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"}, ...}
```

`transcripts/035/anthropic-response.json` — what the client received:

```json
{"content":[{"type":"text","text":"trunc"}], "stop_reason":"end_turn", ...}
```

**The control** (`control-upstream-response.json` + `control-anthropic-response.json`)
is the pair that makes this a defect rather than a normal value: a complete,
untruncated turn whose client body carries the **same `end_turn`**. Same client-side
value, opposite verdict, decided entirely by the upstream.

| Upstream | Client was told | Verdict | |
|---|---|---|---|
| `incomplete` / `max_output_tokens` | `end_turn` 5/5 | violation, should be `max_tokens` | ❌ |
| `completed` | `end_turn` 5/5 | conformant, `end_turn` is correct | ✅ |

## Root cause

Not pinned to a line. Same canonical → Anthropic response mapping as 034 and 036;
the truncation signal is not carried onto `stop_reason` and the mapping falls back
to `end_turn`.

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| The client is told `end_turn` for a truncated turn | **High** | Both halves frozen, 5/5 |
| An untruncated turn is correctly `end_turn` | **High** | Control pair, conformant 5/5 |
| `max_tokens` is the correct value | **High** | It is Anthropic's dedicated truncation reason |
| Same fallback as 034/036 | **Medium** | Consistent across three signals; not source-verified |
| Named source location | **Not established** | Behavioural isolation only |
| Live-provider behaviour | **Untested** | Offline mock upstream, no keys |

Note the mock signals truncation in both the chat-shape and Responses-shape
spellings, so the result does not depend on which one Bifrost reads.

## How real the bug is

Real and consequential, because truncation is the stop reason with a defined
recovery path and this bug removes the trigger for it. Long-generation callers —
document drafting, code output, structured extraction — silently receive cut-off
results and treat them as complete. Downstream parsers fail on the truncated tail
and the failure is attributed to the model producing malformed output.

Bounded by requiring the caller to actually hit the ceiling, which makes it
intermittent: the same prompt behaves correctly until output grows past
`max_tokens`.

## Test

`bifrost_reports_truncation_as_end_turn` (the frozen violation) and
`bifrost_untruncated_turn_reported_as_end_turn_is_fine` (the control), using the new
`truncation_preserved(upstream, client)` checker.

The checker takes **both** payloads deliberately. One that read only the client body
would have to treat every `end_turn` as a violation — flagging every finished turn,
and making a passing control impossible to write. Taking the upstream is what turns
"this response says `end_turn`" into "this response says `end_turn` about a turn that
was cut off".

Invariant: *a turn the upstream truncated is never reported to an Anthropic client
as `end_turn`.*

## Reproducing

```bash
cd transcripts/bifrost-rig
python3 capture_upstream.py &
npx -y @maximhq/bifrost -app-dir . -port 8080 &
python3 hunt.py
```
