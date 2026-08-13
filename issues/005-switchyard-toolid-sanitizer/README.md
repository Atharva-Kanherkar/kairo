# 005, Switchyard's tool-call id sanitizer is lossy and non-injective

- **Upstream**: [Switchyard#178](https://github.com/NVIDIA-NeMo/Switchyard/issues/178) (OPEN).
- **Tool under test**: Switchyard `switchyard-server` 0.2.0, built from a
  2026-08-12 shallow clone (release build; workspace MSRV locally lowered
  1.96.1→1.96.0 to build on Homebrew rustc, a declaration-only change).
- **Config**: `tools/switchyard-route.toml`, Anthropic `/v1/messages` ingress,
  `openai_chat` target → OpenRouter `moonshotai/kimi-k2` (passthrough route).
- **Reproduced**: 2026-08-12. Wire evidence in `transcripts/005/`.

## What breaks (CONFIRMED)

Kimi K2's native tool-call id contains `.` and `:`. Sending the same tool call
through Switchyard's Anthropic ingress mangles it:

```
native id (direct from OpenRouter)  : functions.list_skills:0
returned by Switchyard /v1/messages : functions_list_skills_0
```

(`transcripts/005/kimi-native.json` vs `transcripts/005/turn1.json`.)

`sanitize_anthropic_tool_use_id` replaces every char outside
`[A-Za-z0-9_-]` with `_`. That makes the transform **non-injective and
irreversible**: both `.` and `:` collapse to `_`, so
`functions.list_skills:0` and (hypothetically) `functions:list_skills.0` both
become `functions_list_skills_0`, there is no inverse, and nothing in the
codebase stores the original to restore it. The resulting id *does* satisfy
Anthropic's `^[a-zA-Z0-9_-]+$` (that is the point of sanitizing), but it is no
longer the model's own id.

## Blast radius is backend-dependent (honest scope)

Multi-turn replay of the mangled id (`transcripts/005/turn2*.json`) **succeeded**
here: OpenRouter-served Kimi tolerated the changed `tool_call_id` and produced
the correct final answer (`end_turn`, "Python, Rust, and SQL"). Switchyard
logged `status=200` on all turns.

Issue #178 reports a hard multi-turn break, that was **vLLM-served** Kimi,
whose `kimi_k2` parser is strict about the id matching the model's original.
So: the corruption is real and always happens; whether it *breaks the
conversation* depends on how strictly the backend re-validates the id. A
lossless layer must not depend on backend leniency.

## Test invariants

1. A tool-call id emitted by the backend must round-trip **unchanged** through
   the translator, OR the translator must store the original and restore it
   before replaying upstream (injective, reversible mapping).
2. If sanitization is unavoidable for a target dialect, the original id must be
   preserved in side-channel state keyed to the sanitized one, never
   discarded.

## Repro

```
tools/switchyard/target/release/switchyard-server --config tools/switchyard-route.toml --port 9000
# native id:
curl -s https://openrouter.ai/api/v1/chat/completions -H "authorization: Bearer $OPENROUTER_API_KEY" \
  -d @... | jq -r '.choices[0].message.tool_calls[0].id'      # functions.list_skills:0
# through switchyard:
curl -s localhost:9000/v1/messages -H 'anthropic-version: 2023-06-01' \
  -d @transcripts/005/anthropic-turn1.json | jq -r '.content[]|select(.type=="tool_use").id'  # functions_list_skills_0
```
