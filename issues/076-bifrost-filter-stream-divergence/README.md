# 076, Bifrost reports the same filtered turn as `end_turn` non-stream but `refusal` stream

- **Upstream**: novel, no matching ticket found. Sibling of 034 (non-stream
  `content_filter` reported as `end_turn` on v1.6.11) and 036 (refusal erased
  to `content: []` + `end_turn`). The streaming tool-call regression
  [bifrost#6123](https://github.com/maximhq/bifrost/issues/6123) (open, same
  shape as #3638 fixed by #3640 in v1.5.4) shows the same serializer family
  regressing per transport. Search terms used: `content_filter end_turn`,
  `stop_reason refusal stream`, `incomplete_details`, `stop_reason end_turn
  tool_use`; target version 1.8.6; date checked 2026-09-12.
- **Tool under test**: Bifrost gateway **1.8.6**, commit `1d89501`, built from
  source (`go build ./bifrost-http` in `transports/` with a stub `ui/`
  directory for the embed; `-ldflags "-X main.Version=1.8.6-1d89501"`).
  `/api/version` reports `"1.8.6-1d89501"`. Route `/anthropic/v1/messages`,
  OpenAI-compatible upstream (the route drives it through the Responses API).
- **Reproduced**: 2026-09-12, offline capture rig (`transcripts/076/`, port
  9921 mock, port 8080 gateway), no provider keys. **5/5 deterministic** on
  every cell, with three control cells 5/5 conformant.

## What breaks

The upstream safety-filters a turn (Responses `status: "incomplete"` with
`incomplete_details: {"reason": "content_filter"}`, text `"blocked"`). The
Anthropic client is told two different things depending only on transport:

| Mode | Client receives | Correct? |
|---|---|---|
| non-stream | `stop_reason: "end_turn"`, `content: [{"type": "text", "text": "blocked"}]` | NO, safety erased, reads as a normal answer |
| stream | `message_delta.delta.stop_reason: "refusal"` | YES |

The non-stream turn is indistinguishable from a model that answered `"blocked"`
on its own. A caller cannot tell the model was filtered, and cannot show the
user why, because the reason says the turn finished naturally.

Who it hurts: anyone who builds evals in one mode and serves in the other.
Agent frameworks stream by default and score offline non-stream for
determinism and cost. An eval built from streaming production traffic records
this turn as a refusal; the same turn re-scored offline non-stream records it
as a clean `"blocked"` answer. The quality bar is set from garbage: a cheap
candidate that gets filtered more often looks better on cost and latency while
its refusals score as valid completions, so the unsafe route qualifies and
moves traffic. An agent loop switching on `stop_reason` (`tool_use` executes,
`refusal` shows a blocked message, `end_turn` accepts) takes different
branches for the same turn behind the same gateway. For a cascade (cheap first
pass, escalate on uncertainty), the non-stream path never escalates a filtered
turn because it looks done, while the stream path does, so the routing
decision flips with transport and cost/quality numbers are incomparable.

## Wire evidence

`transcripts/076/` (first iteration frozen; `results.json` holds all 5):

- `filter-nonstream.json`, the violation: `{"stop_reason": "end_turn",
  "content": [{"type": "text", "text": "blocked"}]}`.
- `filter-stream.sse`, the same turn over stream: terminal
  `message_delta` with `"stop_reason": "refusal"`.
- `plain-nonstream.json` / `plain-stream.sse`, controls: an unfiltered turn
  is `end_turn` on both transports, 5/5.
- `control-openai-filter.json`, control: the OpenAI route on the same gateway
  and turn reports `"finish_reason": "content_filter"`, 5/5, so the upstream
  really did filter and the loss is isolated to the non-streaming Anthropic
  serializer.
- `mock_upstream.py`, `config.json`, `hunt.py`, the rig. The stream frames end
  in `response.completed` carrying the SAME response object the non-stream
  path returns, so the only variable is transport. `capture.jsonl` shows all
  20 Anthropic-route requests reaching the same `/v1/responses` upstream path.

### Control matrix (5 iterations each, same upstream turn)

| Route | Mode | Expected | Observed | |
|---|---|---|---|---|
| `/anthropic/v1/messages` | non-stream, filter | `refusal` | `end_turn` 5/5 | FAIL |
| `/anthropic/v1/messages` | stream, filter | `refusal` | `refusal` 5/5 | PASS |
| `/anthropic/v1/messages` | non-stream, plain | `end_turn` | `end_turn` 5/5 | PASS |
| `/anthropic/v1/messages` | stream, plain | `end_turn` | `end_turn` 5/5 | PASS |
| `/v1/chat/completions` | non-stream, filter | `content_filter` | `content_filter` 5/5 | PASS |

## Root cause

Pinned to source lines in the tested commit. The non-streaming converter at
`core/providers/anthropic/responses.go:4986-4995` uses `StopReason` when
present and falls back to `end_turn` (plus a `tool_use` check); it never
consults `IncompleteDetails`. The streaming converter at
`core/providers/anthropic/responses.go:3844-3854` uses `StopReason` when
present and otherwise maps `IncompleteDetails` through
`anthropicStopReasonFromIncompleteDetails`
(`core/providers/anthropic/utils.go:2666-2677`), which maps `content_filter`
to `refusal`. A native Responses terminal carries no `StopReason`, only
`IncompleteDetails`, so the two paths diverge on exactly the shape real
filtered turns arrive in.

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| Non-stream reports `end_turn` for the filtered turn | High | Own captured bytes, 5/5 |
| Stream reports `refusal` for the same turn | High | Own captured bytes, 5/5 |
| The upstream really filtered | High | OpenAI-route control keeps `content_filter`, 5/5 |
| Not the gateway's normal behaviour | High | Plain-turn controls agree on both transports, 5/5 |
| Same upstream path and object both modes | High | `capture.jsonl`: 20/20 Anthropic-route requests to `/v1/responses` |
| Named source location | Medium | Code read matches the observed split exactly; fix not attempted |
| Live-provider behaviour | Untested | Offline mock only; mock speaks the Responses dialect the route uses |

Ruled out: version drift (pinned commit, version endpoint confirms),
configuration mistakes (one default config for all cells, controls pass),
model nondeterminism (deterministic mock), mock-only behavior (OpenAI control
shows the filter is real on the same gateway), malformed input (the
`status: "incomplete"` shape is spec-valid; an earlier run with the 034-era
`status: "completed"` shape reproduced identically).

## How real the bug is

Real, and worse than 034 alone. Either half is a safety bug; the pair is an
evaluation-integrity bug: the verdict depends on transport, so a safety eval
measured streaming disagrees with the same eval measured non-streaming, and
no single number describes what the gateway does with filtered turns. It is
bounded by requiring the upstream to filter, which not every turn does, and a
deployment serving only one mode sees only that mode's behavior. That bound is
exactly what makes it dangerous for a router that observes in one mode and
qualifies in the other.

Bug-or-not: the expected behavior is the spec (Anthropic `stop_reason` has
`refusal` and no `content_filter`; the gateway's own streaming path maps this
input to `refusal`, proving intent). No maintainer commit, PR, comment, or
denylist classifies non-stream `end_turn` as intended. The trigger is
supported usage (documented route, default settings, production filter event).
A real boundary is crossed (a safety constraint disappears on one transport;
an eval gate built on the other mode cannot see it). The fix ships in one
sentence: consult `IncompleteDetails` in the non-streaming converter as the
streaming path already does. Label: `bug`.

## Test

`crates/harness/tests/conformance.rs`:

- `bifrost_anthropic_nonstream_erases_filter_to_end_turn`, the frozen
  violation, reusing the 010A/034 `content_filter_preserved` checker unchanged.
- `bifrost_anthropic_stream_reports_filter_as_refusal`, pinning the fixed
  transport with the new `anthropic_stream_safety_stop_reason` checker plus a
  vacuity guard.
- `bifrost_anthropic_plain_turn_agrees_across_transports`, controls proving
  the checkers distinguish filtered from finished, with a vacuity guard.
- `bifrost_openai_route_control_keeps_content_filter`, proving the upstream
  filtered.

Invariant: *a turn the upstream safety-filtered never reaches the client as a
clean `end_turn`, on either transport, and both transports agree.*

## Reproducing

```bash
cd transcripts/076
python3 mock_upstream.py &
/tmp/bifrost-186 -app-dir . -port 8080 &
python3 hunt.py
```
