# 088, agentgateway's Anthropic to OpenAI streaming tool call emits `"type": null`, and the openai-python accumulator raises before the agent sees a completion

- **Upstream**: [agentgateway/agentgateway](https://github.com/agentgateway/agentgateway). No
  open or closed issue found on 2026-09-27 for a null `tool_calls[].type` on this path.
  The closest relative is [#1988](https://github.com/agentgateway/agentgateway/issues/1988)
  ("Bedrock streaming: tool_call deltas emit `arguments: null` on first chunk"), **closed
  2026-06-02 as fixed**. That is the same defect class on a sibling path, and the
  reasoning below shows why its fix does not cover this one.
- **Tool under test**: agentgateway `v1.5.0`, release binary
  `agentgateway-darwin-arm64`, `git_revision fe6732474a96a0363dfb9822859af4e9bab360fa`,
  `rust_version 1.98.0`, sha256 `da432d35…9dfa11a1` verified against the published
  checksum. Standalone mode, one bind per egress provider.
  Upstream dependency pinned by that release:
  `async-openai` from `github.com/howardjohn/async-openai` rev `a1a4ce7dde6c3f8d5747e27c70d72c48df95fdd3`.
- **Consumer under test**: `openai-python` **2.48.0**, the official SDK, on CPython 3.9.6.
- **Reproduced**: 2026-09-27, macOS arm64. **8 of 8.**
- **Label**: `bug`.

## What breaks

An application points its OpenAI Chat Completions client at agentgateway and routes the
request to an Anthropic provider. The model calls a tool. The model's arguments stream
back as several partial-JSON deltas, which is normal, and the client is expected to stitch
them together.

agentgateway opens the tool call correctly:

```json
{"index":0,"id":"toolu_01","type":"function","function":{"name":"get_weather"}}
```

and then, on **every argument continuation delta**, re-sends `"type": null`:

```json
{"index":0,"id":null,"type":null,"function":{"arguments":"{\"ci"}}
```

`type` is not an ordinary optional string. It is the tag of a discriminated union, and
streaming accumulators treat it as last-write-wins rather than merge-if-present. The
official SDK's merge helper says so in its own source:

```python
# openai/lib/streaming/_deltas.py:23-25
if key == "index" or key == "type":
    acc[key] = delta_value
    continue
```

So the `null` does not get ignored. It **erases** the `"function"` that the opening delta
announced. The accumulator then hits its own consistency check:

```python
# openai/lib/streaming/chat/_completions.py:407
if prev_tool.type == "function":
    assert new_tool.type == "function"
```

and raises `AssertionError`.

The consequence is that the agent loop never receives a completed message. Not a degraded
tool call, not a malformed argument, not a 5xx: an SDK assertion, raised inside the
application process, before `get_final_completion()` returns. The turn is dead, and the
model gets blamed for a value the gateway corrupted after the model produced it correctly.

The damage is specific to streams. The same route with `stream: false` returns a complete,
correct tool call, and so does the same-dialect passthrough route.

## Wire evidence

`transcripts/088/upstream.sse`, what the provider sent. This is a faithful Anthropic
tool-use stream, and it matches the shape in Anthropic's own published streaming
documentation for both the basic and the tool-use case: `input_tokens` in `message_start`,
`output_tokens` in `message_delta`, arguments as `input_json_delta` partials.

```text
event: message_start
data: {"type":"message_start","message":{...,"usage":{"input_tokens":10,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_01","name":"get_weather","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"ci"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"ty\":\"sf\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":5}}

event: message_stop
data: {"type":"message_stop"}
```

`transcripts/088/observed.sse`, what the gateway emitted to the client, verbatim. The
defect is on the two continuation lines, marked `# <-- "type": null` by this writeup and
otherwise untouched:

```text
data: {"id":"msg_1","choices":[{"index":0,"delta":{"content":null,"tool_calls":[{"index":0,"id":"toolu_01","type":"function","function":{"name":"get_weather"}}],"role":"assistant"}}],...}

data: {"id":"msg_1","choices":[{"index":0,"delta":{"content":null,"tool_calls":[{"index":0,"id":null,"type":null,"function":{"arguments":"{\"ci"}}]}}],...}      # <-- "type": null

data: {"id":"msg_1","choices":[{"index":0,"delta":{"content":null,"tool_calls":[{"index":0,"id":null,"type":null,"function":{"arguments":"ty\":\"sf\"}"}}]}}],...}   # <-- "type": null

data: {"id":"msg_1","choices":[{"index":0,"delta":{"content":null},"finish_reason":"tool_calls"}],...,"usage":{"prompt_tokens":0,"completion_tokens":5,"total_tokens":5}}

data: [DONE]
```

Note what is *not* wrong here, because a report that hides its own controls is not
evidence: the tool-call id round-trips exactly (`toolu_01`), the two argument fragments
reassemble to the correct `{"city":"sf"}` when read directly, `finish_reason` is
`tool_calls`, and the lifecycle is a single well-formed stream. Only the null tag is wrong.

`transcripts/088/expected.sse`, what a lossless pipe must emit. Byte-identical to
`observed.sse` except that the two continuation deltas omit the `type` key instead of
nulling it, which is what OpenAI itself emits:

```text
data: {"id":"msg_1","choices":[{"index":0,"delta":{"content":null,"tool_calls":[{"index":0,"id":"toolu_01","type":"function","function":{"name":"get_weather"}}],"role":"assistant"}}],...}

data: {"id":"msg_1","choices":[{"index":0,"delta":{"content":null,"tool_calls":[{"index":0,"function":{"arguments":"{\"ci"}}]}}],...}

data: {"id":"msg_1","choices":[{"index":0,"delta":{"content":null,"tool_calls":[{"index":0,"function":{"arguments":"ty\":\"sf\"}"}}]}}],...}
```

`transcripts/088/control-passthrough.sse`, the same tool call in real OpenAI shape,
delivered through agentgateway's same-dialect passthrough route. No violation, and the SDK
completes normally. This is the control that isolates **conversion** as the trigger rather
than the gateway, the SDK, the model, or the network.

## Consumer evidence

`transcripts/088/consumer/consumer.py` drives the real SDK. Same SDK, same rig, same
upstream capture, two routes:

```text
==========================================================================
conv :4003  body keys: ['max_tokens', 'messages', 'model', 'tools']
==========================================================================
[conv :4003] stream=True, raw chunk iteration
  raw chunk[4].usage              {"completion_tokens": 5, "prompt_tokens": 0, "prompt_tokens_details": null, "total_tokens": 5}
  chunks received                  4
  chunks carrying usage            1
  reassembled tool calls           {"0": {"args": "{\"city\":\"sf\"}", "id": "toolu_01", "name": "get_weather"}}
[conv :4003] stream=True, client.chat.completions.stream()
  SDK RAISED                         AssertionError:
  raised at                          openai/lib/streaming/chat/_completions.py:407
  source line                        assert new_tool.type == "function"

==========================================================================
ctrl :4002  body keys: ['max_tokens', 'messages', 'model', 'tools']
==========================================================================
[ctrl :4002] stream=True, raw chunk iteration
  raw chunk[6].usage              {"completion_tokens": 5, "prompt_tokens": 10, "prompt_tokens_details": null, "total_tokens": 15}
  chunks received                  6
  chunks carrying usage            1
  reassembled tool calls           {"0": {"args": "{\"city\":\"sf\"}", "id": "toolu_01", "name": "get_weather"}}
[ctrl :4002] stream=True, client.chat.completions.stream()
  final.usage                      {"completion_tokens": 5, "prompt_tokens": 10, "prompt_tokens_details": null, "total_tokens": 15}
  final tool_calls                 ['get_weather']
  final arguments                  ['{"city":"sf"}']
```

Two things to read off this. The naive `for chunk in stream:` loop **succeeds** on the
failing route, reassembling the tool call correctly, because the SDK's plain chunk iterator
does not merge. It is the accumulating API, `client.chat.completions.stream()`, the one
that both the official quickstart and every cost- and tool-parsing layer above it uses,
that dies. A gateway can therefore pass a smoke test and still break production. And the
control route, one variable away, completes with the right tool call and the right usage.

Buffered control on the same conversion, same upstream, `stream: false`:

```text
[conv :4003] stream=False
  response.usage                   {"completion_tokens": 5, "prompt_tokens": 10, "prompt_tokens_details": {"cache_write_tokens": 3, "cached_tokens": 7}, "total_tokens": 15}
  tool_calls                       ['get_weather']
  finish_reason                    tool_calls
```

### The byte is sufficient and necessary, with no gateway in the loop

`transcripts/088/consumer/isolate.py` serves hand-built streams that differ from
`observed.sse` in exactly one field, and `variants.py` drives the same SDK against each.
This decides the SDK's behaviour by bytes alone, so nothing about agentgateway can be
blamed or credited for the result:

```text
variant            client.chat.completions.stream()             detail
------------------------------------------------------------------------------
agw_verbatim       RAISED                                       AssertionError at openai/lib/streaming/chat/_completions.py:407
type_omitted       OK                                           OK  prompt=10 total=15 tool=['get_weather']
type_repeated      OK                                           OK  prompt=10 total=15 tool=['get_weather']
type_null_only     OK                                           OK  prompt=10 total=15 tool=['get_weather']
```

`agw_verbatim` is `observed.sse` as captured. `type_omitted` and `type_repeated` are the two
acceptable encodings. `type_null_only` keeps `"id": null` and drops only `type`, which
proves `"id": null` is harmless and the fault is specifically the union tag. The smallest
trigger in the whole investigation is one field on one chunk.

`transcripts/088/isolate/accumulate_delta-proof.txt` closes the loop by calling the SDK's own
accumulator directly, with no HTTP at all:

```text
openai-python 2.48.0
Calling the SDK's own accumulator on the two byte sequences, no HTTP.

agentgateway observed.sse (continuation deltas carry "type": null)
  accumulated tool_calls[0]['type'] = None
  accumulated arguments            = '{"city":"sf"}'
  new_tool.type == 'function'      -> False   (the assert at _completions.py:407)

expected.sse (continuation deltas omit type, as OpenAI emits)
  accumulated tool_calls[0]['type'] = 'function'
  accumulated arguments            = '{"city":"sf"}'
  new_tool.type == 'function'      -> True   (the assert at _completions.py:407)
```

## Root cause

Two sites, one of them the incomplete half of a previous fix.

**1. The encoder leaves the tag unset.**
`crates/llm/src/conversion/messages.rs:836-844`, the `input_json_delta` arm of the
Anthropic to Chat Completions stream converter:

```rust
dr.tool_calls = Some(vec![completions::ChatCompletionMessageToolCallChunk {
    index: ongoing.tool_index,
    id: None,
    r#type: None,
    function: Some(completions::FunctionCallStream {
        name: None,
        arguments: Some(partial_json),
    }),
}]);
```

**2. The wire type serializes `None` as an explicit `null`.**
`ChatCompletionMessageToolCallChunk` is not defined in agentgateway. It comes from the
pinned `async-openai` fork, `src/types/chat/chat_.rs:1120-1128`:

```rust
#[derive(Debug, Deserialize, Serialize, Clone, PartialEq)]
pub struct ChatCompletionMessageToolCallChunk {
    pub index: u32,
    pub id: Option<String>,
    pub r#type: Option<FunctionType>,
    pub function: Option<FunctionCallStream>,
}
```

No `#[serde(skip_serializing_if = "Option::is_none")]` on `id`, `r#type`, or `function`.

**Why the earlier fix does not cover it.** Issue #1988 reported exactly this shape, on the
Bedrock path, where `function.arguments` serialized as `null`. It was resolved upstream in
[async-openai#561](https://github.com/64bit/async-openai/pull/561), merged 2026-06-01, whose
stated scope was to add `skip_serializing_if` to `FunctionCallStream::name` and
`FunctionCallStream::arguments`. The PR notes that "64 of ~90" `Option` fields in that file
already carry the attribute, and it fixed two of the remaining ones on **that** struct. It
did not touch the sibling `ChatCompletionMessageToolCallChunk`, which is where `r#type`
lives. The wire confirms the fix is live and partial: on `observed.sse` the `function`
object correctly contains only `name` on the opening delta and only `arguments` on the
continuations, while `tool_calls[0]` still carries the keys `id`, `index`, `type` on every
chunk.

So this is a known defect class with a known fix, shipped for one struct and not the
adjacent one.

**The same defect is present on a second path, by code inspection only.**
`crates/llm/src/conversion/bedrock.rs:1350` sets `r#type: None` on Bedrock tool-call
continuation deltas, so the Bedrock to Chat Completions streaming conversion should produce
the same `"type": null`. This was **not** measured, because standing up a Bedrock upstream
needs request signing, which this rig does not do. It is recorded as a strong prediction,
not a finding.

**A one-line fix a maintainer would ship.** Either set the tag rather than clearing it, in
`messages.rs:839`:

```rust
r#type: Some(completions::FunctionType::Function),
```

or add the module-conventional attribute in the fork, which also covers `bedrock.rs:1350`
and any future path:

```rust
#[serde(skip_serializing_if = "Option::is_none")]
pub r#type: Option<FunctionType>,
```

Omitting the key matches what OpenAI emits and is the more conservative choice. The `id`
field should get the same treatment for consistency, though it is not load-bearing here.

## Bug or not

- **Is the expected behavior really the spec?** Yes, and unusually well attested. OpenAI's
  chat completion stream sends `type` on the opening tool-call delta and omits it on
  continuations. The openai-python merge helper documents `type` as a union tag that is
  overwritten rather than merged. agentgateway's own #1988 concluded that a `null` where a
  field should be absent is a bug and fixed it for the neighbouring struct. Every source
  here agrees, and none of them is a docstring.
- **Have maintainers already ruled on it?** Yes, in the project's favour and against the
  bug: #1988 is the same class, is closed as fixed, and its resolution path is the fix this
  report recommends. The residual is an incomplete application of that fix.
- **Is the trigger supported usage?** Yes. `/v1/chat/completions` with `stream: true` and a
  `tools` array is the documented default path for an OpenAI-compatible gateway, and
  routing it to an Anthropic backend is a first-class advertised capability. No secret in
  a public field, no disabled control, no exotic configuration. The four ports in the rig
  differ only in which provider the route points at.
- **Is a real boundary crossed?** The gateway is trusted to carry a client's protocol
  faithfully. It corrupts a field in that protocol and the failure surfaces as an
  application-process exception rather than an HTTP error, so the operator sees a stack
  trace in their agent and a 200 in their gateway logs.
- **What fix would a maintainer ship?** One line, as above. Not a doc edit, not "do not do
  that", not an unsupported configuration.

## Upstream status

Searched on 2026-09-27 in `agentgateway/agentgateway` with `gh search issues` on: `type
null tool_calls stream`, `AssertionError stream accumulator`, `chat.completions.stream tool
call type`, `streaming tool call delta`, `new_tool.type`, `prompt_tokens usage stream`,
`input_tokens`. Results:

- **No match** for a null `tool_calls[].type` on any path. Nothing open, nothing closed.
- **#1988**, closed 2026-06-02, same class on the Bedrock path, described above. This is
  the related work, not a duplicate.
- **#2760**, closed, `llm.toolCalls` dropped for streaming responses when client and
  provider formats differ. A different failure (the calls vanish rather than carrying a
  null tag) on the same conversion family. This report's case keeps the calls intact.
- **#3041**, open, buffered conversions undercounting cached input tokens in the gateway's
  own telemetry. Different layer, and see the second defect below.

Classification: **novel**, with one adjacent closed relative whose fix left this residue.

Not verified: `main` at `7e47ceb5` and `v1.6.0-alpha.2` were not built, so this may already
be fixed on a newer revision. Only the `v1.5.0` release binary was executed.

## Second defect on the same code path

Distinct root cause, same function, recorded here rather than filed separately because it
shares the reproduction. On the same streaming route the emitted usage chunk is built from
the `message_delta` event's own field only:

```rust
// crates/llm/src/conversion/messages.rs:909
prompt_tokens: usage.input_tokens.unwrap_or_default() as u32,
```

while the internal accumulator at `messages.rs:750` correctly captured `input_tokens` from
`message_start`. Anthropic's documented stream shapes put `input_tokens` in `message_start`
and send only `output_tokens` in `message_delta`, so the client receives `prompt_tokens: 0`
and a `total_tokens` that omits input entirely, visible in the `raw chunk[4].usage` line
above. Measured 5 of 5 on the wire, and now also at the consumer boundary. The gateway's own
metric is correct on every one of those requests
(`agentgateway_gen_ai_client_token_usage_sum{gen_ai_token_type="input"}`), so this is **not**
a billing bypass and must not be reported as one; the error is only on the client wire,
where SDKs and downstream routers read cost.

The same site also skips the codebase's own normalization helper,
`CacheTokenConvention::include_cache_tokens` at `crates/llm/src/lib.rs:288`, which exists to
convert a provider input count into one that includes cached tokens. The project has
committed a golden snapshot that enshrines the un-normalized value:
`crates/llm/src/tests/response/anthropic/stream_message_delta_usage.messages-completions-streaming.snap`
emits `prompt_tokens: 3883` alongside `cached_tokens: 30464`, which is impossible under
OpenAI semantics, where `cached_tokens` is a subset of `prompt_tokens`. That test is green
only because its input fixture happens to place `input_tokens` in `message_delta`, the one
shape the buggy expression handles. Filed separately; the fixture-provenance lesson is
recorded in the architecture chapter rather than here.

## Test

`crates/harness/src/checks.rs`, `openai_stream_toolcall_type_never_null`. The invariant is
stated as a property of the wire, not as a description of this bug: in an OpenAI Chat
Completions stream, `tool_calls[].type` is a union tag and is never an explicit `null`.

`crates/harness/tests/conformance.rs` asserts the verdict in both directions against frozen
bytes: `Violation` on `transcripts/088/observed.sse`, `Conformant` on
`transcripts/088/expected.sse` (same stream, key omitted), `Conformant` on
`transcripts/088/control-passthrough.sse`, and `Conformant` on a text-only stream so the
checker cannot report a violation for the absence of the construct.

## Reproduce

```bash
# 1. upstream capture, a faithful Anthropic tool-use stream
#    (transcripts/088/upstream.sse)
python3 transcripts/088/consumer/capture_upstream.py 9990 /tmp/upstream.jsonl \
  transcripts/088/upstream.sse &

# 2. agentgateway v1.5.0, standalone, one port per egress provider
#    (transcripts/088/consumer/agentgateway-config.yaml)
agentgateway -f transcripts/088/consumer/agentgateway-config.yaml &

# 3. the real consumer. Route 4003 converts Anthropic->OpenAI and raises;
#    route 4002 is the same-dialect passthrough control and completes.
python3 -m pip install -r transcripts/088/consumer/requirements.txt
python3 transcripts/088/consumer/consumer.py conv   # AssertionError at _completions.py:407
python3 transcripts/088/consumer/consumer.py ctrl   # completes, tool call intact

# 4. the byte in isolation, no gateway in the loop
python3 transcripts/088/consumer/isolate.py 9997 agw_verbatim &
python3 transcripts/088/consumer/variants.py 9997
```

Requires no provider credential. Every upstream byte is served by the local capture
upstream, so the reproduction is deterministic and rerunnable offline.

## Limitations

- One SDK version, `openai-python` 2.48.0. kairo finding 085 records that the equivalent
  Responses-API accumulator changed behaviour across 3.13.0 to 3.19.2, so the affected
  version range for this Chat Completions path is not established and may be narrower or
  wider than 2.48.0 alone suggests.
- Only CPython. The TypeScript SDK was not tested; its accumulator is a separate
  implementation and may or may not share the failure.
- The Bedrock path (`conversion/bedrock.rs:1350`) is a code-inspection prediction only.
- `main` and `v1.6.0-alpha.2` were not built.
- The MCP, A2A, guardrail, and retry surfaces of this target were not probed.
- Only the pinned `v1.5.0` darwin-arm64 release binary was executed. Nothing was built
  from source.
