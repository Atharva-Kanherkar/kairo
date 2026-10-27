# 091, agentgateway re-opens a closed `tool_use` block when text interleaves with a tool call's arguments, so one tool call reaches the agent twice

- **Upstream**: [agentgateway/agentgateway](https://github.com/agentgateway/agentgateway).
  No open or closed issue found on 2026-10-01 for a reused Anthropic content-block
  index or a duplicated `tool_use` block. Searched `content_block_start duplicate`,
  `duplicate tool_use block`, `streaming tool_use index reuse`, `block index reused
  stream`, `interleaved text tool stream`, and `content between tool arguments`.
- **Not a regression of #2147.** That issue and its fix #2148 cover the case where
  an OpenAI-compatible upstream interleaves an **empty** `content` delta with tool
  argument chunks, which closed the `tool_use` block early. The fix filters empty
  strings at `completions.rs:648`. This finding requires a **non-empty** text delta
  between argument chunks, which that filter deliberately does not touch. Both cases
  are reproduced side by side below, on the same binary and the same route.
- **Tool under test**: agentgateway `v1.5.0`, release binary
  `agentgateway-darwin-arm64`, `git_revision fe6732474a96a0363dfb9822859af4e9bab360fa`.
  Independently reproduced on `v1.6.0-alpha.2`, `git_revision
  02110e2ae82e37e72fd55702bcad19921c630218`. The faulty code is byte-identical on
  current `main` at `dd7a4b3`.
- **Consumer under test**: `anthropic` Python SDK **0.125.0**, the official client,
  using its accumulating `messages.stream()` helper.
- **Reproduced**: 2026-10-01, macOS arm64, keyless capture rig. **10 of 10** per
  binary, offline.
- **Label**: `bug`.

Source line numbers are for `v1.5.0`.

## What breaks

An Anthropic client streams a turn through agentgateway to an OpenAI
Chat-Completions upstream. The model emits one tool call and, mid-arguments, also
emits some text. agentgateway closes the tool block to interleave the text, then
re-opens the same tool block when arguments resume.

The client receives **three** content blocks for **one** upstream tool call, and the
first and third share block index `0`:

```text
content_block_start  index=0  tool_use  id=call_a  name=get_weather
content_block_delta  index=0  input_json_delta  {"city":
content_block_stop   index=0
content_block_start  index=1  text
content_block_delta  index=1  text_delta  " and also "
content_block_stop   index=1
content_block_start  index=0  tool_use  id=call_a  name=get_weather   <-- index reused
content_block_delta  index=0  input_json_delta  "sf"}
content_block_stop   index=0
message_delta        stop_reason=tool_use
```

Two independent contract violations in one stream:

1. **A block index is opened twice.** Anthropic clients key accumulated partial
   state by index. A second `content_block_start` at a used index re-opens a block
   the client already closed.
2. **One tool call became two blocks sharing id `call_a`.** The arguments arrive as
   two separate `input_json_delta` runs, `{"city":` and `"sf"}`. Concatenated they
   are the intended `{"city":"sf"}`, but they belong to two different blocks, so a
   client accumulates two partial tool calls. Neither half is valid JSON on its own.

The affected workflow is any agent loop that reads a streamed Anthropic response to
decide what to execute. The official SDK does not raise; it faithfully reports the
duplication:

```text
STOP: tool_use
  block: tool_use call_a get_weather
  block: text            ' and also '
  block: tool_use call_a get_weather
```

An agent that dispatches every `tool_use` block it receives therefore calls
`get_weather` twice, once with `{"city":` and once with `"sf"}`. For a read-only
tool that is a duplicate call. For a side-effecting tool, a payment, a file write,
or a shell command, it is the same class of defect as kairo 085 and 087: the side
effect runs more than once because the transport duplicated the call.

The trigger is an upstream that interleaves text with a tool call's argument
deltas. That shape is legal on the OpenAI wire, where `delta.content` and
`delta.tool_calls` are independent fields. **I did not observe a live provider
produce it** (see "What was not verified"), so the frequency is unquantified. The
translation defect itself is unconditional and deterministic once the shape
occurs.

## Wire evidence

Upstream stream, `transcripts/091/rig/upstream-nonempty-text-mid-args.sse`, one tool
call with a text delta between its argument chunks:

```text
data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}
data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_a","type":"function","function":{"name":"get_weather","arguments":"{\"city\":"}}]},"finish_reason":null}]}
data: {"choices":[{"index":0,"delta":{"content":" and also "},"finish_reason":null}]}
data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"sf\"}"}}]},"finish_reason":null}]}
data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}
```

Client stream, `transcripts/091/rig/observed.sse`, verbatim, `HTTP 200`, complete
transfer. Full bytes in the file; the block lifecycle is quoted above.

Expected: either the text block placed after the tool block, or the tool arguments
buffered until the call is complete and emitted as one block. Both preserve "one
tool call, one block, one index".

### Controls, same process, same route

| Case | Upstream shape | Client result |
|---|---|---|
| Failing | non-empty text between argument chunks | 3 blocks, index 0 opened twice, `call_a` twice |
| Control A | **empty** text delta between argument chunks (#2147 shape) | 1 block, no index reuse, conformant |
| Control B | text **before** the tool call | 2 blocks, indices 0 and 1, conformant |
| Control C | text and `tool_calls` in the **same** delta | 2 blocks, arguments coalesced correctly |
| Control D | same interleaving, **buffered** `stream:false` | 1 `tool_use`, complete valid `{"city":"sf"}` |
| Control E | two distinct parallel tool calls | 2 blocks, distinct ids, conformant |

Control A is the discrimination that matters: it is the case upstream already fixed,
it passes, and the only difference from the failing case is empty versus non-empty
text. That difference is one `!s.is_empty()` filter, and it is the filter that lets
this defect through.

Control D isolates streaming. The buffered path handles the identical upstream turn
correctly, so the fault is in the stream state machine and not in the request or
response translation that both paths share.

## Root cause

`crates/llm/src/conversion/completions.rs:409-444`, `open_tool_block`:

```rust
let index = *state
    .tool_block_indices
    .entry(tool_index)
    .or_insert_with(|| {
        let idx = state.next_block_index;
        state.next_block_index += 1;
        idx
    });

// Keep each tool-use block open across interleaved deltas to avoid
// emitting duplicate start events for the same block index.
if state.open_tool_blocks.insert(tool_index) {
    push_event(&mut events, ContentBlockStart { index, .. });
}
```

The comment states the invariant the code then breaks. Two pieces of state are
mismatched:

- `tool_block_indices` **caches** the index for a tool call for the whole stream, so
  a re-opened block reuses its original index. That part is correct on its own.
- `open_tool_blocks` is **cleared** by `close_all_tool_blocks`, which
  `open_text_block` calls. So after a text delta interrupts the tool call, the set no
  longer contains the tool index, the `insert` returns `true`, and a second
  `content_block_start` is emitted at the cached index.

The result is a start event for an index the client has already seen stopped, and a
second `tool_use` block carrying the same id.

`completions.rs:648` is where the fix for #2147 lives:

```rust
if let Some(content) = choice.delta.content.as_deref().filter(|s| !s.is_empty()) {
```

Empty deltas are dropped before they can open a text block. Non-empty ones are not,
and they close the open tool block, which is the trigger.

Identical on `v1.6.0-alpha.2` and on `main` at `dd7a4b3`.

## Bug or not

- **Is the expected behavior really the spec?** Yes. The Anthropic Messages
  streaming contract gives each content block an `index` and clients accumulate by
  it; a block is started once and stopped once. The strongest evidence is inside this
  repository's own target: the comment above the faulty code states the intent to
  avoid duplicate start events for the same block index. The code contradicts its
  own documented intent.
- **Have maintainers already ruled on it?** No, and the nearest ruling points the
  other way. #2147 reported this exact failure mode, "tool_use blocks close early
  when empty content deltas interleave with tool_calls", and #2148 fixed the empty
  case. That is a maintainer treating premature block closure in this state machine
  as a defect, not as intended behavior. Nothing classifies non-empty interleaved
  text as acceptable.
- **Is the trigger supported usage?** Yes, for a gateway whose stated job is
  translating OpenAI-shaped streams to Anthropic SSE. Default config, no flags. The
  upstream shape is legal on the OpenAI wire. The caveat is frequency, addressed
  below, and it bears on usefulness rather than on whether the code is wrong.
- **Is a real boundary crossed?** No security boundary. The correctness boundary is:
  the gateway tells the client it delivered one tool call and delivers two partial
  ones.
- **What fix would a maintainer ship, in one sentence?** Track whether a
  `content_block_start` has already been emitted for a given tool-call id, and if so
  keep appending `input_json_delta` to the existing block instead of re-opening it,
  so an interleaved text delta suspends the tool block rather than restarting it.
- **Label**: `bug`

## Test

`anthropic_stream_block_lifecycle` in
[`crates/harness/src/checks.rs`](../../crates/harness/src/checks.rs). It encodes two
rules: a block index is opened at most once, and a tool-call id appears in exactly
one `tool_use` block.

The split rule matches on **tool-call id**, not on block count or on the presence of
any media. That is deliberate: a checker that looks for "some non-`text` block
anywhere" can be satisfied by unrelated content while the tool call itself is split.
An adversarial test asserts exactly that case.

[`crates/harness/tests/adversarial.rs`](../../crates/harness/tests/adversarial.rs) is
new and exists because a checker that cannot fail certifies nothing. Ten cases, each
built to break the checker in a specific way:

- a reused index is detected
- a two-way and a three-way split are both detected, and the run count is reported
- **an unrelated `image` block elsewhere in the stream cannot mask a split**
- two distinct tool calls conform, so a legitimate parallel call is not flagged
- text before a tool call conforms, and text between two distinct calls conforms
- a `tool_use` with an empty input and no deltas conforms, so the checker does not
  invent a split
- an index reopened after `content_block_stop` is caught even when the first block
  was text
- empty, malformed, and index-less input is ignored rather than misread or panicked on

That file caught a real bug in my own first draft: the reused-index branch returned
early, so a stream exhibiting both defects reported only one. Both rules now fire on
the recorded bytes, and the conformance test asserts both.

Conformance tests in
[`crates/harness/tests/conformance.rs`](../../crates/harness/tests/conformance.rs):
the recorded violation, and the empty-delta control as `Conformant`.

## Reproduce

Cold start, offline, no credential:

```bash
cd transcripts/091/rig
chmod +x reproduce.sh
curl -sL -o agentgateway https://github.com/agentgateway/agentgateway/releases/download/v1.5.0/agentgateway-darwin-arm64
chmod +x agentgateway
./reproduce.sh ./agentgateway
```

Verified `REPRODUCED` on `fe673247` and `02110e2a`. The script refuses to start if
its ports are taken, asserts the listener is the process it launched, verifies the
capture upstream is serving the intended file before trusting any observation, bounds
every curl, and treats an incomplete transfer as a failure rather than a pass.

Falsifiability was checked directly. Replacing the failing upstream stream with a
correct one, where text precedes the tool call rather than interleaving with its
arguments, makes the script report `NOT REPRODUCED CLEANLY` and exit `1`, with the
determinism rung at `0 of 10`. A reproduction script that cannot fail proves nothing.

## What was not verified

- **No live provider was observed producing this shape.** I tested OpenAI
  (`gpt-4o-2024-08-06`) directly and through OpenRouter across five models, forcing
  tool calls with `tool_choice`. None interleaved text with tool-call arguments in
  these samples. So how often this occurs in production is unquantified, and the
  user-visible impact is demonstrated on recorded wire bytes and the official SDK
  rather than on a live model run. A maintainer may reasonably weigh that heavily.
- The Anthropic-egress and Responses-egress binds were not re-measured for this
  finding; the Responses bind consumes a different wire shape and the buffered
  control (D) already isolates streaming.
- Only one Anthropic SDK version (0.125.0) was exercised. A client that raises on a
  reused index would turn this from a silent duplication into a hard failure, which
  would be worse, not better.
- Upstream status was checked on 2026-10-01 only.
