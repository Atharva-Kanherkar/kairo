# 067, LiteLLM erases structured refusal text on Anthropic translation

- **Upstream**: no matching LiteLLM issue found in the current GitHub tracker
  on 2026-09-03. Issues about streaming refusal events, finish reasons, and
  empty input blocks describe different routes or failures and are not cited
  here.
- **Tool under test**: LiteLLM **1.99.0**.
- **Reproduced**: 2026-09-03 on macOS arm64 with a keyless local OpenAI capture
  backend. The Anthropic translation failed **5/5** times; the same-process
  OpenAI Chat control passed **5/5** times.

## What breaks

The upstream returns a completed OpenAI Responses message whose only content
part is:

```json
{"type":"refusal","refusal":"REFUSALPROBE cannot help"}
```

When an Anthropic client calls LiteLLM's `/v1/messages` route, LiteLLM sends
that request to the configured `/v1/responses` backend and receives the refusal
above. The client gets HTTP 200 semantics with:

```json
{"content":[],"stop_reason":"end_turn"}
```

The refusal text and its refusal representation are gone. An agent or UI sees a
successful but blank assistant turn and cannot tell that the model declined the
request.

## Wire evidence

Each JSONL record contains one complete trial: the request body LiteLLM sent
upstream, the upstream response body as captured, and the client response body.
Credential-bearing headers were removed; the candidate retains LiteLLM's
`litellm/1.99.0` user agent.

- `transcripts/067/litellm-anthropic-refusal-loss.jsonl`: five Anthropic
  translation trials. Every upstream `body_raw` contains
  `REFUSALPROBE cannot help`; every client response omits it and has
  `content: []`.
- `transcripts/067/litellm-openai-refusal-control.jsonl`: five OpenAI Chat
  ingress controls through the same LiteLLM process. Every upstream response
  carries the same refusal, and every client response preserves it at
  `choices[0].message.provider_specific_fields.refusal`.

| Route | Upstream refusal | Client result | Verdict |
|---|---|---|---|
| Anthropic `/v1/messages` to Responses backend | present 5/5 | absent, `content: []` 5/5 | violation |
| OpenAI Chat to Chat backend | present 5/5 | preserved as structured refusal 5/5 | control passes |

## Root cause (if found)

Not pinned to a source line. The wire proves the loss occurs after LiteLLM
receives a valid Responses refusal and before it emits the Anthropic client
body. No source-level mechanism is claimed.

## Test

`refusal_text_preserved` compares upstream refusal strings with every string
representation available to the client. It catches the erased candidate and
accepts the structured OpenAI Chat control. The candidate also reuses
`response_content_not_empty` from issue 036 to freeze the blank Anthropic turn.

- `litellm_drops_structured_refusal_on_anthropic_translation` replays all five
  violations.
- `litellm_openai_route_keeps_structured_refusal` replays all five controls.

Invariant: refusal text returned by the upstream must remain representable in
the client response, even when the client and backend use different dialects.

Replay both sides offline:

```bash
cargo test -p kairo --test conformance litellm_drops_structured_refusal_on_anthropic_translation
cargo test -p kairo --test conformance litellm_openai_route_keeps_structured_refusal
```
