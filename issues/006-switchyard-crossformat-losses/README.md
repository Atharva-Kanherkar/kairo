# 006 — Switchyard cross-format translation: four silent field losses

- **Upstream**: matches the lossy paths named in the research audit of
  Switchyard source (policy.rs / codec files); no single ticket — these are
  by-design "AllowWithDiagnostics" losses, reproduced on the wire.
- **Tool under test**: Switchyard `switchyard-server` 0.2.0 (2026-08-12 build).
- **Method**: Switchyard's backend pointed at a local **capture server**
  (`tools/capture_server*.py`) that records the exact bytes Switchyard emits
  upstream and returns a canned reply. Fully offline, deterministic, no keys.
- **Reproduced**: 2026-08-12. Evidence: `transcripts/014/capture*.jsonl`.

All four below are **silent** — HTTP 200, no error to the client.

## Loss 1 — `is_error` dropped (Anthropic → OpenAI)

An Anthropic `tool_result` with `is_error: true` becomes a plain OpenAI
`role:"tool"` message. The forwarded body contains no error marker anywhere
(`is_error present: False`). The model can no longer tell the tool call
**failed** vs succeeded — it sees "permission denied" as if it were a normal
result.

## Loss 2 — system blocks flattened + `cache_control` dropped

Two Anthropic `system` blocks (the second carrying
`cache_control: {type: ephemeral}`) are flattened into **one** OpenAI system
message, joined with `\n\n`. `cache_control` is gone
(`cache_control present: False`). Prompt caching is **silently disabled** — a
real, recurring cost increase with no signal to the caller.

## Loss 3 — `max_tokens` invented as 64000 (OpenAI → Anthropic)

An OpenAI request with **no** `max_tokens` (legal — OpenAI treats it as
optional) is translated to Anthropic, which **requires** `max_tokens`.
Switchyard fabricates `max_tokens: 64000`. The client never chose this; it
changes truncation behavior and cost ceiling invisibly.

## Loss 4 — `n` dropped (OpenAI → Anthropic)

An OpenAI request with `n: 2` (two completions) is forwarded to the Anthropic
target with **no** `n` field (Anthropic has no equivalent). The client asked
for two candidates and silently gets one. No error, no diagnostic in the
response.

## Not a loss (kept honest)

`max_tokens: 100` (Anthropic → OpenAI) correctly became
`max_completion_tokens: 100` — value preserved, only renamed. Good behavior,
recorded so the corpus isn't one-sided.

## Test invariants

1. `is_error` on a tool result MUST survive translation (as `is_error`, or an
   equivalent the target model actually sees).
2. `cache_control` / prompt-cache directives MUST either survive or the caller
   MUST be told caching was dropped.
3. A required target field the client did not supply (e.g. Anthropic
   `max_tokens`) MUST be surfaced as a decision, not silently invented.
4. A dropped request feature with no target equivalent (`n>1`) MUST be
   reported, not silently discarded.

## Repro

```
tools/litellm-env/bin/python tools/capture_server.py $PWD/transcripts/014/capture.jsonl &
tools/switchyard/target/release/switchyard-server --config tools/switchyard-capture.toml --port 9000 &
curl -s localhost:9000/v1/messages -H 'anthropic-version: 2023-06-01' -d @transcripts/014/rich.json
# then inspect transcripts/014/capture.jsonl — the last body is what Switchyard sent upstream
```
