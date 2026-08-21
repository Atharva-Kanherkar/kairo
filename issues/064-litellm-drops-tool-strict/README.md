# 064, LiteLLM `/v1/messages` drops function-tool `strict`

- **Upstream**: no matching LiteLLM issue found on 2026-08-21. Adjacent:
  [litellm#27490](https://github.com/BerriAI/litellm/issues/27490) is the
  reverse OpenAI-to-Anthropic path, where `strict` survives but is placed in
  the wrong location. This finding is Anthropic-to-Responses, where the field
  disappears entirely.
- **Tool under test**: LiteLLM 1.96.2, configured as an OpenAI-compatible
  backend behind the local capture server.
- **Reproduced**: 2026-08-21. Keyless local capture. Five of five client
  calls returned HTTP 200. Evidence: `transcripts/064/`.

## What breaks

An Anthropic client can require strict function-tool arguments with:

```json
{
  "tools": [{
    "name": "strict_probe",
    "strict": true,
    "input_schema": {"type": "object", "additionalProperties": false}
  }]
}
```

LiteLLM's Anthropic `/v1/messages` ingress translates that request to OpenAI
Responses, but the forwarded function tool has no `strict` field. The wire no
longer represents a caller-supplied constraint. The client receives HTTP 200
and no diagnostic.

The same LiteLLM process preserves `tools[].function.strict: true` when entered
through `/v1/chat/completions`, and a direct Responses request preserves the
top-level function-tool `strict: true`. This is an Anthropic-ingress translation
loss, not a backend-format limitation.

## Wire evidence

1. **LiteLLM Anthropic ingress**
   - `transcripts/064/litellm-messages-strict-upstream.jsonl`
   - Five `/v1/messages` calls became `/v1/responses` requests. Each tool keeps
     its name, description, and JSON Schema, but omits `strict`. Every caller
     response was HTTP 200 (`transcripts/064/litellm-messages-results.json`).
2. **Control: LiteLLM OpenAI ingress**
   - `transcripts/064/litellm-openai-strict-control.jsonl`
   - The same proxy forwards `tools[0].function.strict: true` and returns HTTP
     200.
3. **Control: direct Responses target shape**
   - `transcripts/064/responses-strict-direct-control.jsonl`
   - A native Responses function tool carries top-level `strict: true`.

## Translation boundary

LiteLLM's Anthropic Messages to Responses path omits `tools[].strict` from the
observed upstream body. The field has a native Responses equivalent, and
LiteLLM's OpenAI ingress shows the same proxy can forward it unchanged.

## Test

`tool_strict_forwarded` checks the target's exact function-tool shape: top-level
`strict: true` for Responses or nested `function.strict: true` for OpenAI Chat.

- `litellm_drops_anthropic_tool_strictness` freezes the five-run violation.
- `litellm_openai_route_keeps_tool_strictness` and
  `responses_tool_strictness_direct_control` freeze both controls.

Invariant: a client-supplied strict function-tool constraint reaches the
upstream in the target format's function-tool field.

## Repro

```bash
python3 transcripts/064/repro.py
# The keyless rig starts an in-memory capture server on 9996, LiteLLM on 4000,
# sends five Anthropic calls plus the OpenAI control, verifies all HTTP 200
# results, and prints the captured upstream bodies.
```
