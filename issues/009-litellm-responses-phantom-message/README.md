# 009, LiteLLM /v1/responses bridge emits a phantom empty message item on every tool call

- **Tool under test**: LiteLLM 1.96.2, OpenAI `/v1/responses` ingress →
  `gemini/gemini-3-flash-preview`.
- **Reproduced**: 2026-08-12, 3/3 deterministic.
  Evidence: `transcripts/probe/resp009-{1,2,3}.json`.

## What breaks

A `/v1/responses` request that forces a tool call returns an `output` array
whose **first** item is a `message` with null text, followed by the actual
`function_call`:

```json
[
  {"type":"message","content":[{"type":"output_text","text":null,"annotations":[]}]},
  {"type":"function_call","name":"get_weather", ...}
]
```

The leading `message` carries `output_text.text: null`, a phantom, empty
assistant message that the model never produced. 3/3 runs identical.

## Why it matters

Clients that iterate Responses `output` items (the Vercel AI SDK, the OpenAI
Agents SDK, Codex) render or process each item. A `message` item with
`text: null`:

- renders as an empty assistant turn in chat UIs, or
- throws on clients that assume `output_text.text` is a string, not null.

This is the same phantom-empty-block class seen on the `/v1/messages` path
(issue 004) and mirrors the empty-content-chunk problem that breaks strict SSE
clients elsewhere. A lossless bridge must not fabricate output items the model
did not emit.

## Test invariants

1. The translated `output` array MUST contain only items the model actually
   produced; no fabricated empty `message` items.
2. `output_text.text` MUST be a string when a `message` item is present; a
   null-text message item is invalid.

## Repro

```
tools/litellm-env/bin/litellm --config tools/litellm-config.yaml --port 4000  # gemini3 route
curl -s localhost:4000/v1/responses -H 'content-type: application/json' -d '{
  "model":"gemini3","input":"weather in Paris",
  "tools":[{"type":"function","name":"get_weather","description":"w","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}],
  "tool_choice":"required"}' | jq '[.output[]|{type,text:.content[0].text}]'
# -> first item is {"type":"message","text":null}
```
