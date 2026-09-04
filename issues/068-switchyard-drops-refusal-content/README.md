# 068, Switchyard erases structured refusal text on Anthropic translation

- **Upstream**: no matching NVIDIA-NeMo/Switchyard ticket found in the current
  GitHub tracker on 2026-09-04. The only search result mentioning refusals,
  [#598](https://github.com/NVIDIA-NeMo/Switchyard/issues/598), concerns
  multimodal judge routing and is unrelated to response translation.
- **Tool under test**: Switchyard main commit **`9523023`**, reporting
  `switchyard-server` **0.2.0**.
- **Reproduced**: 2026-09-03 on macOS arm64 with a keyless local OpenAI Chat
  capture backend. Anthropic translation failed **5/5** times; the same-process
  OpenAI Chat response path preserved the refusal **5/5** times.

## What breaks

The OpenAI Chat upstream returns a completed assistant message containing:

```json
{"content":null,"refusal":"REFUSALPROBE cannot help"}
```

When an Anthropic client calls Switchyard's `/v1/messages` route, the same
refusal reaches Switchyard from its configured `/v1/chat/completions` backend.
The client receives HTTP 200 semantics with only this content block:

```json
{"content":[{"type":"text","text":""}],"stop_reason":"end_turn"}
```

Switchyard erases the refusal text and invents an empty Anthropic text block.
The caller cannot tell that the model declined the request and instead sees a
successful assistant turn containing an empty string.

This is not issue 045. Issue 045 starts with an upstream tool-only response and
invents an empty text block *before* a preserved `tool_use`; issue 068 starts
with an upstream refusal, loses the refusal, and emits only the invented empty
text block. It is also separate from issue 036: that issue records Bifrost
erasing a Responses refusal into `content: []`. Here the gateway is Switchyard,
the downstream body contains an invented block rather than an empty array, and
the control carries the same refusal through Switchyard's OpenAI path.

## Wire evidence

Each JSONL record contains one complete trial: the request Switchyard forwarded
to its OpenAI Chat backend, the backend response captured verbatim, and the
client response. Credential-bearing headers were removed.

- `transcripts/068/switchyard-anthropic-refusal-loss.jsonl`: five Anthropic
  translation trials. Every upstream `body_raw` contains
  `REFUSALPROBE cannot help`; every client response omits it and contains
  exactly `[{"type":"text","text":""}]`.
- `transcripts/068/switchyard-openai-refusal-control.jsonl`: five OpenAI Chat
  response-path controls through the same Switchyard process. The same refusal
  is present in every upstream response and every client response.

| Client path | Upstream refusal | Client result | Verdict |
|---|---|---|---|
| Anthropic `/v1/messages` | present 5/5 | absent; only empty text 5/5 | violation |
| OpenAI `/v1/chat/completions` | present 5/5 | preserved as `message.refusal` 5/5 | control passes |

## Root cause (if found)

Not pinned to a source line. The wire proves the loss occurs after Switchyard
receives a valid Chat Completions refusal and before it emits the Anthropic
client body. No source-level mechanism is claimed.

## Test

`refusal_text_preserved` compares every upstream `refusal` string with every
string representation available to the client. It does not assume the target
dialect keeps a `refusal` field: preserving the text in an ordinary content
block is also conformant.

- `switchyard_drops_structured_refusal_on_anthropic_translation` replays all
  five violations and separately freezes the invented empty text block.
- `switchyard_openai_route_keeps_structured_refusal` replays all five controls.

Invariant: refusal text returned by the upstream must remain representable in
the client response, even when the client and backend use different dialects.

Replay both sides offline:

```bash
cargo test -p kairo --test conformance switchyard_drops_structured_refusal_on_anthropic_translation
cargo test -p kairo --test conformance switchyard_openai_route_keeps_structured_refusal
```
