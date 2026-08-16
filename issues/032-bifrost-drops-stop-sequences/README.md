# 032, Bifrost drops `stop_sequences` on the Anthropic ingress

- **Upstream**: no ticket found. New in this repo; nearest relative is kairo 006,
  the class of request fields that vanish in translation.
- **Tool under test**: Bifrost gateway **v1.6.11**, `npx -y @maximhq/bifrost`.
- **Reproduced**: 2026-08-16, offline capture rig (`transcripts/bifrost-rig/`),
  no provider keys. **5/5**, with the control passing 5/5.

## What breaks

A client sends `stop_sequences: ["STOPPROBE"]` to `/anthropic/v1/messages`. A stop
sequence is a hard generation boundary: the caller is saying *cut the output the
moment this string appears*. It is how callers keep a model from running past the
end of a structured block, from continuing a conversation it should not simulate,
or from emitting a delimiter the caller uses to frame the response.

Bifrost forwards a request with no stop field of any kind. The model generates past
the boundary, and the client receives text it explicitly asked not to be given.

## Wire evidence

`transcripts/032/upstream-request.jsonl` — what Bifrost forwarded:

```json
{"model":"mimo-v2.5","max_output_tokens":100,"input":[{"type":"message","role":"user","content":[{"type":"input_text","text":"hi"}]}]}
```

Top-level keys are exactly `input`, `max_output_tokens`, `model`. `STOPPROBE`
appears nowhere in the forwarded body.

`transcripts/032/control-openai-upstream.jsonl` — **the control**: the same gateway
and upstream, entered through `/v1/chat/completions` with `stop: ["STOPPROBE"]`,
forwards `stop` intact.

| Route | Client asked | Reached upstream | |
|---|---|---|---|
| `/anthropic/v1/messages` | `stop_sequences: ["STOPPROBE"]` | absent 5/5 | ❌ |
| `/v1/chat/completions` | `stop: ["STOPPROBE"]` | present 5/5 | ✅ |

The gateway carries stop sequences perfectly well on one route and silently discards
them on the other.

## Root cause

Not pinned to a line. Localized to the Anthropic → canonical request translation:
`stop_sequences` has a direct equivalent on the OpenAI side (`stop`), which the
gateway already emits on its own OpenAI route, so this is a missing mapping rather
than an unrepresentable concept.

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| `stop_sequences` never reaches the upstream | **High** | Capture is ground truth, 5/5 |
| A mapping exists and is simply not applied | **High** | The OpenAI route emits `stop` 5/5 |
| Named source location | **Not established** | Behavioural isolation only |
| Live-provider behaviour | **Untested** | Offline mock upstream, no keys |

## How real the bug is

Real, and worse than it first reads because it is unobservable. A dropped stop
sequence does not error; it produces a longer, plausible-looking completion. A
caller parsing to a delimiter gets extra text, a caller using stops to prevent
role-play gets the role-play, and the failure looks like the model ignoring
instructions rather than the proxy discarding them. Bounded by affecting only
callers that set stop sequences.

## Test

`bifrost_drops_stop_sequences` (the frozen violation) and
`bifrost_openai_route_keeps_stop_sequences` (the control), using the new
`stop_sequence_forwarded` checker.

Invariant: *a client-supplied stop sequence appears in the request the gateway
forwards upstream.*

## Reproducing

```bash
cd transcripts/bifrost-rig
python3 capture_upstream.py &
npx -y @maximhq/bifrost -app-dir . -port 8080 &
python3 hunt.py
```
