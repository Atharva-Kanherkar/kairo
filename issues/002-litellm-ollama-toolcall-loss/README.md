# 002 — LiteLLM×Ollama: wrong finish_reason (stream) and total tool-call loss

- **Upstream**: [litellm#35663](https://github.com/BerriAI/litellm/issues/35663)
  (OPEN — reproduced exactly), family: litellm#35711, litellm#31911,
  ollama#7881 (index symptom NOT reproduced — fixed in Ollama 0.32.9).
- **Tools under test**: LiteLLM 1.96.2 (current) → Ollama 0.32.9, qwen3:4b.
- **Reproduced**: 2026-08-12, macOS. Both defects live on CURRENT versions.
- **Wire evidence**: `transcripts/002/` (LiteLLM routes), `transcripts/006/`
  (direct-Ollama baseline, healthy).

## Defect A — `ollama_chat/` stream mislabels finish_reason (litellm#35663, exact)

Same OpenAI-format request, route `ollama_chat/qwen3:4b`:

| | non-stream | stream |
|---|---|---|
| tool call | present ✓ | delta chunk present ✓ (id, name, args, index) |
| finish_reason | `tool_calls` ✓ | **`stop`** ❌ |

The tool_calls delta and the finish chunk are separate events; the finish
chunk is built without knowledge that a tool call was emitted. Every OpenAI-SDK
agent loop branches on `finish_reason == "tool_calls"` — under streaming the
loop ends instead of executing the tool. Non-stream vs stream disagree on the
same request: invariant 4 violated.

## Defect B — `ollama/` route annihilates the tool call entirely

Same request, route `ollama/qwen3:4b` (Ollama's OpenAI-compat surface behind
LiteLLM):

- **stream**: zero `tool_calls` chunks; the model's tool-call JSON leaks out
  as `delta.reasoning_content` fragments (`"}"` etc.), then `finish: stop`.
- **non-stream**: `message` contains only `{role, content: ""}` — no
  tool_calls, no content, nothing. The call is destroyed without error.
- **Control**: the identical request sent directly to Ollama's own
  `/v1/chat/completions` works (`transcripts/006/direct-*.json/.sse`:
  tool_calls + `finish_reason: tool_calls` + index present). So the loss is
  LiteLLM's `ollama/` adapter, not the runtime.

Two LiteLLM routes to the same runtime, same model, same request: one returns
a mislabeled tool call, the other returns nothing. This is the
fidelity-varies-by-adapter-path finding (litellm#31911) captured on the wire.

## Test invariants

1. `finish_reason` MUST be `tool_calls` whenever any tool_calls delta was
   emitted in the stream (per-request stream/non-stream agreement).
2. A tool call emitted by the runtime MUST appear as `tool_calls` in the
   client response — never as `reasoning_content`, never silently dropped.
3. Route equivalence: all routes to the same backend must produce semantically
   identical tool calls for the same request.

## Repro

```
ollama pull qwen3:4b
tools/litellm-env/bin/litellm --config tools/litellm-config.yaml --port 4000
curl -sN localhost:4000/v1/chat/completions -H 'content-type: application/json' \
  -d @transcripts/002/req-qwen3-ollama-chat-stream.json     # Defect A
curl -s  localhost:4000/v1/chat/completions -H 'content-type: application/json' \
  -d @transcripts/002/req-qwen3-ollama-compat.json          # Defect B
```
