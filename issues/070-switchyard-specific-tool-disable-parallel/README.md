# 070, Switchyard drops `disable_parallel_tool_use` when a specific tool is forced

- **Upstream**: no dedicated ticket for this shape. Adjacent Switchyard behavior documented in `issues/017-parallel-tool-flag-dropped` (Anthropic `tool_choice: {"type":"auto","disable_parallel_tool_use":true}`) and Switchyard `ToolChoice` enum handling. No matching open issue found on 2026-09-05 for `{"type":"tool","name":...,"disable_parallel_tool_use":true}`.
- **Tool under test**: `switchyard-server 0.2.0` at `952302321c870585c307554ca882abaceca589d7` (Rust 1.96.1, native `openai_chat` backend).
- **Reproduced**: 2026-09-05 on macOS arm64. Keyless local OpenAI Chat capture backend (`tools/sweep/mock.py` style). Five of five client calls returned HTTP 200.

## What breaks

An Anthropic client that forces a specific tool and disables parallel tool use:

```json
{
  "model": "captured-model",
  "max_tokens": 64,
  "messages": [{"role": "user", "content": "hi"}],
  "tools": [{"name": "get_weather", "description": "Get weather", "input_schema": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}],
  "tool_choice": {"type": "tool", "name": "get_weather", "disable_parallel_tool_use": true}
}
```

is forwarded to an OpenAI Chat backend as:

```json
{
  "model": "captured-model",
  "messages": [{"role": "user", "content": "hi"}],
  "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {...}}}],
  "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
  "max_completion_tokens": 64
}
```

`parallel_tool_calls` is absent. The caller-supplied serial constraint is silently lost. The backend is free to emit parallel `tool_use` blocks for the forced tool in a single turn. The client receives HTTP 200 with no diagnostic.

This is not an OpenAI format limitation. The matching OpenAI Chat ingress control through the same Switchyard process and backend forwards `parallel_tool_calls: false` alongside the forced `tool_choice` on all five runs.

## Wire evidence

1. **Switchyard Anthropic ingress (5/5 dropped)**
   - `transcripts/070/anthropic-run1.jsonl` through `anthropic-run5.jsonl` (copied from `transcripts/switchyard-toolchoice-specific-disable-20260905/anthropic-run*.jsonl`)
   - Each file contains `request`, `forwarded` (upstream body), `response`, and `upstream_raw` (capture record). Every forwarded body omits `parallel_tool_calls`.

2. **Control: Switchyard OpenAI Chat ingress (5/5 preserved)**
   - `transcripts/070/control-openai-run1.jsonl` through `control-openai-run5.jsonl`
   - Same Switchyard process, same mock backend, same tool and `tool_choice` name, but via `POST /v1/chat/completions` with `parallel_tool_calls: false` and `tool_choice: {"type":"function","function":{"name":"get_weather"}}`. Every forwarded body retains `parallel_tool_calls: false`.

3. **Minimal trigger (5/5)**
   - Removing only `disable_parallel_tool_use` while keeping `{"type":"tool","name":"get_weather"}` produces the same forwarded shape without `parallel_tool_calls`, now expected. This isolates the loss to the flag itself, not the tool name or the `tool_choice` wrapper.

## Root cause

`crates/switchyard-translation/src/codecs/anthropic/buffered.rs:711-730` decodes `tool_choice` via `decode_anthropic_tool_choice`, which maps any `{"type":...}` object to `ToolChoice::Auto`, `Required`, `None`, or `Tool { name }`, discarding sibling fields such as `disable_parallel_tool_use`. `ToolChoice` (`crates/protocol/src/llm.rs:232`) has no field for the parallel flag, and `tool_choice` is excluded from `provider_extensions`, so the flag is not preserved elsewhere.

`crates/switchyard-translation/src/codecs/openai_chat/buffered.rs:930-953` copies only `parallel_tool_calls` from `extensions` when present. Since the Anthropic decoder never stores the flag, the encoder has nothing to copy.

`crates/switchyard-translation/src/codecs/anthropic/buffered.rs:686-705` similarly constructs `ToolDefinition` with `strict: None`, discarding another Anthropic sibling field in the same manner (see `issues/066`).

## Test

`tool_strict_forwarded` already tests `strict`, and `parallel_tool_disable_preserved` tests the `auto` shape. New invariant: a client-supplied `disable_parallel_tool_use: true` alongside any `tool_choice` type (including `tool(name)`) must survive as `parallel_tool_calls: false` on the OpenAI wire, verified by the same checker with a `tool(name)` probe body.

Repro harness asserts via `capture_records` and `parallel_tool_disable_preserved`:

- `switchyard_specific_tool_disable_parallel_dropped` freezes the 5-run violation (Anthropic `tool(name)+disable`).
- `switchyard_specific_tool_disable_parallel_control` freezes the 5-run OpenAI preservation.

Invariant: an Anthropic `disable_parallel_tool_use: true` reaches the upstream as `parallel_tool_calls: false` regardless of whether `tool_choice` is `auto`, `any`, `none`, or a specific `tool(name)`.

## Repro

Build fresh Switchyard main with Rust 1.96.1, then run the capture rig (no provider credentials):

```bash
# from kairo checkout
python3 /tmp/brute_p1_full.py
# writes 5+5 jsonl files under transcripts/070/ and prints DROPPED vs PRESERVED
```

Or manually:

```bash
tools/switchyard/target/debug/switchyard-server --config /tmp/switchyard.toml --port 9006 &
# Anthropic dropped case (repeat 5x):
curl -s http://127.0.0.1:9006/v1/messages -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"captured-model","max_tokens":64,"messages":[{"role":"user","content":"hi"}],"tools":[{"name":"get_weather","description":"Get weather","input_schema":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}],"tool_choice":{"type":"tool","name":"get_weather","disable_parallel_tool_use":true}}'
# forwarded OpenAI body lacks parallel_tool_calls (check capture mock jsonl)

# Control via OpenAI (preserved):
curl -s http://127.0.0.1:9006/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"captured-model","max_tokens":64,"messages":[{"role":"user","content":"hi"}],"tools":[{"type":"function","function":{"name":"get_weather","description":"Get weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}],"tool_choice":{"type":"function","function":{"name":"get_weather"}},"parallel_tool_calls":false}'
# forwarded body retains parallel_tool_calls:false
```
