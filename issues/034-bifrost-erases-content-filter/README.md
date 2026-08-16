# 034, Bifrost reports an upstream `content_filter` to Anthropic clients as `end_turn`

- **Upstream**: no ticket found. Same class as kairo 010A
  ([Switchyard#369](https://github.com/NVIDIA-NeMo/Switchyard/issues/369)), where the
  same safety signal is erased the same way. Second gateway with this defect.
- **Tool under test**: Bifrost gateway **v1.6.11**, `npx -y @maximhq/bifrost`.
- **Reproduced**: 2026-08-16, offline capture rig (`transcripts/bifrost-rig/`),
  no provider keys. **5/5**, with the control passing 5/5.

## What breaks

The upstream refuses to complete a turn and says so: `finish_reason:
"content_filter"`. Through `/anthropic/v1/messages`, the client is told
`stop_reason: "end_turn"` — *the model finished speaking normally*.

The distinction is the entire point of the field. `end_turn` tells a caller the
answer is complete and can be used, logged, shown to a user, or fed to the next
step. `content_filter` tells it the answer was cut off by a safety system and must
be handled differently: surfaced, retried, escalated, or refused. Erasing it means
a filtered response is consumed as a finished one.

## Wire evidence

`transcripts/034/anthropic-response.json` — what the client received:

```json
{"content":[{"type":"text","text":"blocked"}], "stop_reason":"end_turn", ...}
```

`transcripts/034/control-openai-response.json` — **the control**: the identical
upstream turn, same gateway, requested through `/v1/chat/completions`:

```json
{"choices":[{"finish_reason":"content_filter", ...}]}
```

| Route | Upstream said | Client was told | |
|---|---|---|---|
| `/v1/chat/completions` | `content_filter` | `content_filter` 5/5 | ✅ |
| `/anthropic/v1/messages` | `content_filter` | `end_turn` 5/5 | ❌ |

The signal reaches the gateway intact and survives on one route. Only the Anthropic
response mapping discards it.

## Expected

Not `end_turn`. Anthropic's `stop_reason` enum includes `refusal` for exactly this
situation, so that is the natural target, but the load-bearing claim here is the
negative one: a turn the upstream filtered must not be reported as a turn the model
finished on its own. Any value that preserves the distinction fixes the bug.

## Root cause

Not pinned to a line. Localized to the canonical → Anthropic response mapping,
which appears to fall back to `end_turn` for every finish reason it does not
explicitly handle. Issues 035 and 036 are the same fallback swallowing different
signals, which is why they are filed as siblings rather than duplicates.

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| The client is told `end_turn` | **High** | Our own captured bytes, 5/5 |
| The signal exists at the gateway | **High** | OpenAI-route control, 5/5 |
| Same class as Switchyard 010A | **High** | Identical input and identical erasure |
| `refusal` is the exactly-correct target | **Medium** | Enum fits; not verified against Bifrost's intent |
| Named source location | **Not established** | Behavioural isolation only |

## How real the bug is

The most serious of this batch. It is a safety signal, and its failure mode is the
dangerous direction: filtered content is presented to the caller as a normal,
complete answer. A moderation pipeline keyed on `stop_reason` sees nothing to act
on; a log shows a clean turn. Unlike a dropped request field, this one actively
misinforms the caller rather than quietly under-delivering.

Bounded by requiring an upstream that filters in the first place, and by the fact
that a caller inspecting the response text may still notice.

## Test

`bifrost_erases_content_filter_to_end_turn`, using `content_filter_preserved` —
**the checker written for kairo 010A against Switchyard**, unchanged — plus
`bifrost_openai_route_keeps_content_filter`, which parses the control fixture and
feeds the observed pair back through the checker.

Invariant: *an upstream `content_filter` is never delivered to an Anthropic client
as `end_turn`.*

## Reproducing

```bash
cd transcripts/bifrost-rig
python3 capture_upstream.py &
npx -y @maximhq/bifrost -app-dir . -port 8080 &
python3 hunt.py
```
