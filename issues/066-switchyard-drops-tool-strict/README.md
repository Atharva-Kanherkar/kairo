# 066, Switchyard drops Anthropic function-tool `strict`

- **Upstream**: no matching Switchyard issue, PR, TODO, or public discussion
  found on 2026-08-29. Related [Switchyard#467](https://github.com/NVIDIA-NeMo/Switchyard/issues/467)
  concerns structured-output schema enforcement, not function-tool `strict`.
- **Tool under test**: NVIDIA Switchyard `main` at
  `27fc1ce9ff3846760337fe42ab09c28f5b01c807`.
- **Reproduced**: 2026-08-29 on macOS arm64. Keyless local OpenAI Chat capture
  backend. Five of five client calls returned HTTP 200. Evidence:
  `transcripts/066/`.

## What breaks

An Anthropic client can require schema-valid function-tool inputs by setting
`strict: true` beside its tool's `name`, `description`, and `input_schema`.
When Switchyard translates that request to an OpenAI Chat backend, it forwards
the tool without `function.strict`. The backend receives the schema but not the
caller-supplied enforcement requirement. The client gets HTTP 200 and no
diagnostic.

This is not an OpenAI Chat format limitation. The matching OpenAI Chat ingress
control, through the same Switchyard server and backend, forwards
`function.strict: true` on all five runs.

## Wire evidence

1. **Switchyard Anthropic ingress**
   - `transcripts/066/switchyard-anthropic-strict-upstream.jsonl`
   - Five `/v1/messages` requests with `tools[0].strict: true` became OpenAI
     Chat requests whose named `function` tool omitted `strict`. The name,
     description, and parameters survive. Every client response was HTTP 200
     in `transcripts/066/switchyard-strict-results.json`.
2. **Control: Switchyard OpenAI Chat ingress**
   - `transcripts/066/switchyard-openai-strict-control.jsonl`
   - Five matching `/v1/chat/completions` requests through the same server
     forwarded the named tool with `function.strict: true`.

## Root cause

`decode_anthropic_tools` constructs every normalized `ToolDefinition` with
`strict: None`, discarding the caller's boolean before the OpenAI Chat encoder
can emit it. The encoder already writes `function.strict` whenever the neutral
field is present. See the [decoder](https://github.com/NVIDIA-NeMo/Switchyard/blob/27fc1ce9ff3846760337fe42ab09c28f5b01c807/crates/switchyard-translation/src/codecs/anthropic/buffered.rs#L686-L705)
and [encoder](https://github.com/NVIDIA-NeMo/Switchyard/blob/27fc1ce9ff3846760337fe42ab09c28f5b01c807/crates/switchyard-translation/src/codecs/openai_chat/buffered.rs#L1188-L1194).

## Test

`tool_strict_forwarded` checks the target format's exact field location:
`function.strict: true` for OpenAI Chat.

- `switchyard_drops_anthropic_tool_strictness` freezes the five-run violation.
- `switchyard_openai_route_keeps_tool_strictness` freezes the five-run control.

Invariant: a client-supplied strict function-tool constraint reaches the
upstream in the target format's function-tool field.

## Repro

Build fresh Switchyard main with Rust 1.96.1, then run:

```bash
SWITCHYARD_SERVER=/path/to/switchyard-server python3 transcripts/066/repro.py
```

The script starts a local capture backend and Switchyard, sends the Anthropic
request five times, then sends the OpenAI Chat control five times. It needs no
provider credentials and fails unless all Anthropic captures omit `strict`, all
controls retain it, and all ten client responses are HTTP 200.
