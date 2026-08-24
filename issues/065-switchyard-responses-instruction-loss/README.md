# 065, Switchyard demotes Responses instructions to user messages

- **Upstream**: no matching Switchyard issue found on 2026-08-21. Adjacent
  [Switchyard#496](https://github.com/NVIDIA-NeMo/Switchyard/issues/496) configures
  target prompts; it does not cover client-supplied Responses inline instruction
  roles.
- **Tool under test**: NVIDIA Switchyard `main` at `053a61e`, current local build.
- **Reproduced**: 2026-08-21. Keyless OpenAI Chat capture backend. Five of five
  client calls returned HTTP 200. Evidence: `transcripts/065/`.

## What breaks

OpenAI Responses treats `system` and `developer` messages as higher-priority
guidance than user input. When Switchyard sends a Responses request to an OpenAI
Chat backend, inline `system` and `developer` items become ordinary `user` messages.

The upstream sees only user messages. Policies such as "never call this tool" or
"return JSON only" therefore lose their instruction precedence without an error.
The caller receives HTTP 200.

## Wire evidence

1. **Responses to Chat**
   - `transcripts/065/responses-instruction-loss-upstream.jsonl`
   - Five `/v1/responses` calls forwarded three Chat messages, all with
      `role: user`: `SYSTEM-INSTRUCTION`, `DEVELOPER-INSTRUCTION`, and `USER-INPUT`.
      Each paired client request, upstream body, and HTTP 200 response is retained
      in `transcripts/065/responses-instruction-loss-results.json`.
2. **Control: string instructions**
   - `transcripts/065/responses-string-instruction-control.jsonl`
   - A string `instructions` value becomes a preceding Chat `system` message.

## Root cause

The Responses codec stores decoded inline `system` and `developer` input items as
ordinary messages. The Chat encoder maps ordinary non-assistant messages to `user`.

## Test

`instruction_messages_preserved` requires the forwarded Chat messages to retain
the expected roles, content, and order.

Invariant: client-supplied instruction messages retain their target-format roles
and precedence before reaching the upstream model.

## Repro

Run current Switchyard against a keyless OpenAI Chat capture backend, then POST a
Responses request with inline `system`, `developer`, and `user` input messages.
The captured Chat body contains all three values as `user` messages.
