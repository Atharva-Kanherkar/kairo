# 033, Bifrost drops assistant `thinking` blocks from conversation history

- **Upstream**: no ticket found for this path. Same class as kairo 016, where
  Switchyard drops thinking and LiteLLM leaks it as visible text. Bifrost drops it.
  Adjacent open ticket on a different route:
  [bifrost#5274](https://github.com/maximhq/bifrost/issues/5274)
  (`reasoning_details` silently dropped from incoming OpenAI-compatible requests).
- **Tool under test**: Bifrost gateway **v1.6.11**, `npx -y @maximhq/bifrost`.
- **Reproduced**: 2026-08-16, offline capture rig (`transcripts/bifrost-rig/`),
  no provider keys. **5/5**, with the control passing 5/5.

## What breaks

A client replays a prior assistant turn that contains a `thinking` block plus its
`signature`, exactly as Anthropic's extended-thinking contract requires when
continuing a reasoning conversation. Bifrost forwards the assistant turn with the
visible text only. The thinking block and its signature are gone.

The model therefore continues a chain of reasoning it can no longer see. For
providers that validate thinking signatures on continuation, the turn is also no
longer well-formed history.

## Wire evidence

`transcripts/033/upstream-request.jsonl` — the forwarded assistant item:

```json
{"id":"msg_...","type":"message","status":"completed","role":"assistant",
 "content":[{"type":"output_text","text":"4","annotations":[],"logprobs":[]}]}
```

The client sent `{"type":"thinking","thinking":"THINKPROBE simple arithmetic","signature":"sigabc"}`
followed by the text `4`. Only `4` survives; `THINKPROBE` appears nowhere in the
forwarded body.

**Which failure mode.** It is dropped, not leaked. `thinking_not_leaked_as_visible_text`
is conformant on this fixture, so Bifrost behaves like Switchyard here rather than
like LiteLLM, whose copy of this bug pastes the reasoning into visible output. That
distinction matters: leaking is a privacy problem, dropping is a correctness one.

**The control.** `transcripts/033/control-is-error-upstream.jsonl` shows the same
translator faithfully carrying a different Anthropic-only concept: a tool result
marked `is_error: true` becomes `"status": "incomplete"` on the forwarded
`function_call_output`, and a successful one becomes `"completed"` (5/5 each). So
this translator does map Anthropic-specific semantics onto Responses-API fields
when someone writes the mapping. Thinking simply has none.

## Root cause

Not pinned to a line. Localized to the Anthropic → canonical request translation of
assistant content blocks: `text` and `tool_use` blocks are carried, `thinking` and
its signature are not represented on the outbound item.

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| Thinking never reaches the upstream | **High** | Capture is ground truth, 5/5 |
| Dropped rather than leaked | **High** | Leak checker conformant on the same fixture |
| The translator can carry Anthropic-only semantics | **High** | `is_error` control, 5/5 |
| Named source location | **Not established** | Behavioural isolation only |
| Live-provider behaviour, incl. signature rejection | **Untested** | Offline mock upstream, no keys |

## How real the bug is

Real for extended-thinking workloads and invisible everywhere else. A caller doing
multi-turn reasoning silently loses the chain between turns: answers get worse, and
nothing in the response says why. Whether a live provider additionally *rejects* the
turn for a missing thinking signature is untested here and would raise severity from
quiet degradation to a hard failure.

Bounded honestly: it only affects conversations that carry thinking blocks, and an
OpenAI-shaped upstream has no native place to put them, so a correct fix may be a
provider-dependent mapping rather than a one-line field copy.

## Test

`bifrost_drops_thinking_history` (the frozen violation, using
`thinking_text_forwarded` — **the checker written for kairo 016 against
Switchyard**, unchanged) and `bifrost_thinking_is_dropped_not_leaked` (which pins
the failure mode with `thinking_not_leaked_as_visible_text`).

Invariant: *thinking a client replays as conversation history reaches the upstream,
and never appears as visible assistant text.*

## Reproducing

```bash
cd transcripts/bifrost-rig
python3 capture_upstream.py &
npx -y @maximhq/bifrost -app-dir . -port 8080 &
python3 hunt.py
```
