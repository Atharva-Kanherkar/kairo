# 016, thinking history is destroyed in cross-format request translation

- **Upstream**: no single ticket. Adjacent to Switchyard's own unsigned-thinking strip
  (`libsy-llm-client` `strip_unsigned_thinking_blocks`) and LiteLLM's
  Anthropic-to-Responses adapter. New finding from the offline capture rig.
- **Tools under test**: Switchyard `switchyard-server` 0.2.0 (commit 2bef154);
  LiteLLM 1.96.2.
- **Method**: gateway backend pointed at `tools/capture_server.py` /
  `tools/mock_upstream.py`. No keys. Deterministic.
- **Reproduced**: 2026-08-13. Evidence: `transcripts/016/cap-thinking.jsonl`
  (Switchyard), `transcripts/016/cap-litellm-thinking.jsonl` (LiteLLM).

## What breaks

An Anthropic `/v1/messages` request whose history includes a signed thinking
block:

```
assistant: thinking "simple arithmetic" + signature SIGNATURE_ABC123 + text "4"
```

is forwarded with the thinking destroyed. Two products, two failure modes, same
agent-loop death: the next turn no longer has the model's private reasoning
(or has it in the wrong channel).

### Switchyard (Anthropic ingress, OpenAI Chat backend)

Forwarded assistant message is only `"content": "4"`. No `reasoning_content`,
no signature, no thinking block. The thinking text is gone.

Root cause: `encode_message_without_tool_results_to_openai` filters
`ContentBlock::Reasoning` out of the message and never writes
`reasoning_content` on the request path. (Response encoding does write
`reasoning_content`. Request encoding does not.)

### LiteLLM (Anthropic ingress, OpenAI mock, internally routed through `/v1/responses`)

Forwarded Responses `input` turns the thinking into visible assistant
`output_text`:

```
assistant content: ["simple arithmetic", "4"]
```

The signature is gone. Private reasoning is now part of the visible
transcript the backend model will read. Worse than a drop: it pollutes the
prompt.

## Why it matters

Claude Code, Cline, and any extended-thinking client replay thinking blocks
on later turns. A translator that drops them (Switchyard) or promotes them
to user-visible text (LiteLLM) makes multi-turn reasoning agents silently
wrong. HTTP 200 both times.

## Test invariants

1. Thinking text present on the inbound Anthropic request MUST appear in the
   forwarded body as a reasoning/thinking field, not vanish.
2. Thinking text MUST NOT be rewritten as ordinary assistant `content` /
   `output_text`.
3. A thinking signature MUST either round-trip or be dropped with an explicit
   diagnostic, never silently.

## Repro

```
# Switchyard
python tools/capture_server.py $PWD/transcripts/016/cap-thinking.jsonl &
tools/switchyard/target/release/switchyard-server --config tools/switchyard-capture.toml --port 9000 &
curl -s localhost:9000/v1/messages -H 'anthropic-version: 2023-06-01' \
  -d @transcripts/016/req-thinking.json
# forwarded assistant content is "4"; thinking is gone

# LiteLLM (mock OpenAI that records /v1/responses)
# after pointing LiteLLM at the mock: thinking becomes output_text "simple arithmetic"
```
