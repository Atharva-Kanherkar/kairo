# 001, /v1/messages streaming translation defects, version-dependent

- **Upstream**: [litellm#25390](https://github.com/BerriAI/litellm/issues/25390) (OPEN),
  [litellm#29491](https://github.com/BerriAI/litellm/issues/29491) (OPEN), the
  "streaming drops tool_use.input" class, regressed 5× historically.
- **Tools under test**: LiteLLM 1.96.2 (current) and 1.82.0 (the version #25390
  calls the regression), same config, same requests.
- **Reproduced**: 2026-08-12, macOS, backends: `gemini/gemma-4-31b-it` (the exact
  model in #25390) and `openrouter/moonshotai/kimi-k2`.

## What we found (wire evidence, `transcripts/001/`)

### Defect A, stop_reason mismapped (1.82.0, FIXED by 1.96.2)

Identical Anthropic-format streaming request, Gemma backend, forced tool call:

| | 1.82.0 (`gemma-stream-182.sse`) | 1.96.2 (`gemma-stream.sse`) |
|---|---|---|
| stop_reason | **`end_turn`** ← wrong | `tool_use` ✓ |
| blocks | empty `text` block + tool_use | tool_use only |
| tool args | valid | valid |

An Anthropic-SDK agent loop executes tools only when `stop_reason ==
"tool_use"`. On 1.82.0 the loop terminates silently: the call is present but
never runs. This is taxonomy failure mode 3 (finish-reason mismapping) captured
live, and it co-occurs with a spurious zero-length text block (Defect B) -
the empty-chunk shape that broke the Vercel AI SDK in sglang#29441.

### Not reproduced on current version

The headline symptom of #25390/#29491 (empty `tool_use.input` under streaming)
did **not** reproduce on 1.96.2 in 5/5 large-payload runs (Kimi, ~16KB args,
~690 deltas each: all reassembled to valid JSON, grammar legal) nor on the
exact Gemma model. The open issues were filed against 1.82.x-1.85.x with
different intermediaries (cc-switch) and upstreams (SiliconFlow); the simple
paths appear patched since. The 5× recurrence record still makes this class a
mandatory regression test, that is the point of this corpus.

## Test invariants (to encode in `crates/harness`)

1. If the final message contains a `tool_use` block, `message_delta.stop_reason`
   MUST be `tool_use`.
2. No zero-length content blocks: every `content_block_start` must be followed
   by ≥1 non-empty delta or carry non-empty content.
3. Concatenated `input_json_delta.partial_json` MUST parse as JSON and satisfy
   the tool's `input_schema` (all required keys present).
4. Streaming and non-streaming answers to the identical request MUST agree on
   tool name, argument content, and stop_reason.

## Repro commands

```
tools/litellm-env/bin/litellm     --config tools/litellm-config.yaml --port 4000  # 1.96.2
tools/litellm-env-182/bin/litellm --config tools/litellm-config.yaml --port 4001  # 1.82.0
curl -sN localhost:400{0,1}/v1/messages -H 'content-type: application/json' \
  -H 'anthropic-version: 2023-06-01' -d @transcripts/001/request-gemma-stream.json
tools/litellm-env/bin/python tools/check_anthropic_stream.py 'transcripts/001/*.sse'
```
