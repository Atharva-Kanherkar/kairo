# 043, GoModel drops `disable_parallel_tool_use` on the Anthropic ingress

- **Upstream**: no ticket. Same class as kairo 017 (Switchyard and LiteLLM)
  and 031 (Bifrost). GoModel is the fourth gateway to lose this flag.
- **Tool under test**: ENTERPILOT/GoModel **0.1.77** (`abde73ca`).
- **Reproduced**: 2026-08-17, capture mock, no provider keys. **5/5**, with
  the OpenAI-route control passing 5/5. Evidence: `transcripts/042/`.

## What breaks

A client sends `disable_parallel_tool_use: true` (with `tool_choice: auto`)
to `/v1/messages`. That flag is how a caller says *give me one tool call at
a time*. Agent loops that execute tools sequentially depend on it.

GoModel forwards `tool_choice: "auto"` and emits no `parallel_tool_calls`
field. The instruction is gone, and nothing in the response tells the
client it was ignored.

## Wire evidence

`transcripts/042/gm-parallel-upstream.jsonl` — what GoModel forwarded:

```json
{"model":"captured-model","max_tokens":64,"tool_choice":"auto","tools":[...],"messages":[...]}
```

No `parallel_tool_calls`. The client's `disable_parallel_tool_use: true` is
absent in every form. 5/5.

`transcripts/042/gm-openai-parallel-upstream.jsonl` — **the control**: the
same gateway, same upstream, entered through `/v1/chat/completions` with
`parallel_tool_calls: false`:

```json
{"parallel_tool_calls":false, "tool_choice":"auto", ...}
```

The flag survives there. So the upstream can carry it and GoModel can send
it; only the Anthropic ingress drops it.

| Route | Client asked | Reached upstream | |
|---|---|---|---|
| `/v1/messages` | `disable_parallel_tool_use: true` | `<absent>` 5/5 | ❌ |
| `/v1/chat/completions` | `parallel_tool_calls: false` | `false` 5/5 | ✅ |

## Root cause

Not pinned to a line. Localized to the Anthropic → canonical chat
translation: `tool_choice` is reduced to its `type` string and
`disable_parallel_tool_use` is not carried onto the canonical request, so
the OpenAI encoder has nothing to emit.

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| The flag never reaches the upstream | **High** | Capture is ground truth, 5/5 |
| Specific to the Anthropic ingress | **High** | OpenAI-route control passes 5/5 |
| Named source location | **Not established** | Behavioural isolation only |
| Live-provider behaviour | **Untested** | Offline mock upstream, no keys |

## How real the bug is

Moderate. It is silent and the caller cannot detect it from the response,
so a sequential agent loop can be handed parallel calls with no signal. It
is bounded by mattering only to callers that set the flag, and by the fact
that many models will not emit parallel calls anyway.

## Test

`gomodel_drops_disable_parallel_tool_use` (violation) and
`gomodel_openai_route_keeps_parallel_tool_calls` (control), both using
`parallel_tool_disable_preserved`, the checker written for kairo 017.

Invariant: *a client instruction to disable parallel tool use reaches the
upstream, as `parallel_tool_calls: false` or an equivalent the provider
understands.*
