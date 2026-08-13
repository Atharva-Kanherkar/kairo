# 019, Switchyard invents `cache_control: ephemeral` on every Anthropic backend request

- **Upstream**: no ticket. Source-pinned to
  `libsy-llm-client/src/client.rs` `enable_anthropic_prompt_caching`, called
  unconditionally for `Backend::Anthropic` after encode.
- **Tool under test**: Switchyard 0.2.0 (commit 2bef154).
- **Method**: OpenAI Chat ingress, Anthropic capture backend
  (`tools/switchyard-cap-anth.toml` + `tools/capture_server_anthropic.py`).
  No keys.
- **Reproduced**: 2026-08-13. Evidence:
  `transcripts/016/cap-openai-strict.jsonl`.

## What breaks

The client sent a plain OpenAI chat request:

```
{"model":"cap-anthropic","messages":[{"role":"user","content":"hi"}], ...}
```

No `cache_control`, no prompt-cache fields. Switchyard forwarded to
Anthropic:

```
{"role":"user","content":[{"type":"text","text":"hi","cache_control":{"type":"ephemeral"}}]}
```

`enable_anthropic_prompt_caching` marks the last content block of the last
message as a prompt-cache breakpoint on **every** Anthropic-backend call.
The client did not opt in. Silent, HTTP 200.

This is the inverse of issue 006 (cache_control dropped on the way to
OpenAI). Here a cache breakpoint is **invented** on the way to Anthropic.

## Why it matters

Anthropic prompt caching is a billing and behavior change: it pins a cache
write at that breakpoint, can retain prefixes the caller did not intend to
cache, and interacts with later `cache_control` the client *did* send. A
translator that always sets ephemeral on the last user block changes cost
and cache hit rates with no signal.

Also confirmed on the same capture, already counted under 006: invented
`max_tokens: 64000`, dropped `n`, dropped `strict`, dropped
`parallel_tool_calls`, dropped `seed` / `frequency_penalty` /
`presence_penalty` / `logit_bias`. Same-format LiteLLM OpenAI chat (control)
keeps those fields.

## Test invariants

1. `cache_control` MUST NOT appear on a forwarded Anthropic body unless the
   client sent a cache directive.
2. Inventing a required-looking provider feature (cache breakpoint,
   `max_tokens`) MUST be surfaced as a decision, not applied silently.

## Repro

```
python tools/capture_server_anthropic.py $PWD/transcripts/016/cap-openai-strict.jsonl &
tools/switchyard/target/release/switchyard-server --config tools/switchyard-cap-anth.toml --port 9001 &
curl -s localhost:9001/v1/chat/completions -d @transcripts/016/req-openai-strict.json
# last user text block has cache_control.type=ephemeral; client never sent it
```
