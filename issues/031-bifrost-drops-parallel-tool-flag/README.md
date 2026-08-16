# 031, Bifrost drops `disable_parallel_tool_use` on the Anthropic ingress

- **Upstream**: no ticket found. Same class as kairo 017 (Switchyard and LiteLLM
  both drop this flag); Bifrost is the third gateway to lose it.
- **Tool under test**: Bifrost gateway **v1.6.11**, `npx -y @maximhq/bifrost`.
- **Reproduced**: 2026-08-16, offline capture rig (`transcripts/bifrost-rig/`),
  no provider keys. **5/5**, with the control passing 5/5.

## What breaks

A client sends `tool_choice: {"type":"auto","disable_parallel_tool_use":true}` to
`/anthropic/v1/messages`. That flag is how a caller says *give me one tool call at
a time* — agent loops that execute tools sequentially depend on it, and a caller
that gets two calls back when it asked for one either runs both or has to discard
work it already paid for.

Bifrost forwards `tool_choice: "auto"` to the upstream and emits no
`parallel_tool_calls` field at all. The instruction is gone, and nothing in the
response tells the client it was ignored.

## Wire evidence

`transcripts/031/upstream-request.jsonl` — what Bifrost forwarded:

```json
{"model":"mimo-v2.5","max_output_tokens":100,"tool_choice":"auto","input":[...],"tools":[...]}
```

No `parallel_tool_calls`. The client's `disable_parallel_tool_use: true` is absent
in every form.

`transcripts/031/control-openai-upstream.jsonl` — **the control**: the same gateway,
same upstream, same turn, entered through `/v1/chat/completions` with
`parallel_tool_calls: false`:

```json
{"parallel_tool_calls":false, ...}
```

The flag survives there. So the upstream can carry it and Bifrost can send it; only
the Anthropic ingress drops it.

| Route | Client asked | Reached upstream | |
|---|---|---|---|
| `/anthropic/v1/messages` | `disable_parallel_tool_use: true` | `<absent>` 5/5 | ❌ |
| `/v1/chat/completions` | `parallel_tool_calls: false` | `false` 5/5 | ✅ |

## Root cause

Not pinned to a line. Localized to the Anthropic → canonical request translation:
`tool_choice` is reduced to its `type` string and the sibling
`disable_parallel_tool_use` is not carried onto the canonical request, so the
downstream Responses encoder has nothing to emit.

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| The flag never reaches the upstream | **High** | Capture is ground truth, 5/5 |
| Specific to the Anthropic ingress | **High** | OpenAI-route control passes 5/5 |
| Named source location | **Not established** | Behavioural isolation only |
| Live-provider behaviour | **Untested** | Offline mock upstream, no keys |

## How real the bug is

Moderate. It is silent — no error, no warning — and the caller cannot detect it
from the response, so a sequential agent loop can be handed parallel calls with no
signal. It is bounded by mattering only to callers that set the flag, and by the
fact that many models will not emit parallel calls anyway; the failure surfaces as
occasional double-execution rather than a constant break.

## Test

`bifrost_drops_disable_parallel_tool_use` (the frozen violation) and
`bifrost_openai_route_keeps_parallel_tool_calls` (the control), both using
`parallel_tool_disable_preserved`, **the checker written for kairo 017 against
Switchyard**, unchanged.

Invariant: *a client instruction to disable parallel tool use reaches the upstream,
as `parallel_tool_calls: false` or an equivalent the provider understands.*

## Reproducing

```bash
cd transcripts/bifrost-rig
python3 capture_upstream.py &
npx -y @maximhq/bifrost -app-dir . -port 8080 &
python3 hunt.py
```
