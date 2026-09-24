# 085, LiteLLM Responses mid-stream fallback replays a delivered tool call and corrupts the public stream's output-index namespace

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). No exact
  upstream ticket found on 2026-09-24. Related reports are listed below.
- **Tool under test**: LiteLLM 1.102.1 (current latest release,
  [tag v1.102.1](https://github.com/BerriAI/litellm/releases/tag/v1.102.1)) and
  current main commit
  [`1c8a0ff6`](https://github.com/BerriAI/litellm/commit/1c8a0ff6023b3eba6b01e955c49edcfa97bf0d22)
  (reports as `1.104.0`, unreleased), OpenAI Python SDK 2.54.0.
- **Reproduced**: 2026-09-24 through the real LiteLLM proxy CLI's
  `POST /v1/responses` route, a real `fallbacks` router configuration, and a
  deterministic OpenAI Responses capture upstream. No provider credential is
  needed.

## What breaks

A developer configures LiteLLM's documented `router_settings.fallbacks` for the
Responses API, exactly as
[PR #28215](https://github.com/BerriAI/litellm/pull/28215) added. A model
completes a tool call, then the underlying provider connection fails
(a retriable 5xx). LiteLLM's fallback wrapper decides whether anything was
already delivered by checking only accumulated **text**
(`router.py:3274`, `e.generated_content`). A completed tool call is not text, so
the wrapper treats the turn as if nothing happened and replays the original
input against the fallback deployment. The fallback deployment runs the tool
again and streams its own result into the same public SSE body, restarting
`output_index` at zero.

The official OpenAI Python SDK's `client.responses.stream()` accumulator keeps
one response snapshot per stream and dispatches on `output_index` without ever
noticing the second `response.created`. So a caller of the documented SDK either

- receives a genuinely duplicated tool call (the agent runs a side-effecting
  action twice), or
- crashes at
  [`_responses.py:254` or `:294`](https://github.com/openai/openai-python/blob/v2.54.0/src/openai/lib/streaming/responses/_responses.py#L247-L297)
  with `AssertionError`, if the fallback's item at the reused index has a
  different type than the primary's.

This is not limited to tool calls. The **same** collision crashes the SDK even
when the primary only streamed partial **text** before failing and the fallback
correctly follows LiteLLM's own designed continuation-prompt path
(`router.py:3277`, `_build_responses_continuation_input`). The intended,
non-buggy retry strategy still reuses the wire-level `output_index` namespace
and still corrupts the client's stream.

```text
Client -> LiteLLM /v1/responses (fallbacks: [primary, backup])
       -> primary: function_call "append_ledger" delivered, then a 5xx error
       -> generated_content is text-only, sees "" -> replays original input
       -> backup: runs append_ledger again, own output_index starts at 0
Client <- one SSE body: two response.created, two completed function_call
          items at output_index 0, OR an AssertionError before any final
          response is available
```

Measured impact is 5/5 for each of three distinct triggers, on both the pinned
release and current main, over a real LiteLLM proxy process and the real
`openai` SDK. Two clean controls confirm the discriminating variable: whether
the primary delivered any output item before it failed.

## Wire evidence

Each JSONL line is one complete public exchange over real HTTP: a real
`openai` Python SDK client, driven as a subprocess, calling a real LiteLLM
proxy process, which calls a deterministic local upstream. `client_request` and
`client_response` retain the exact bytes the SDK sent and received.
`upstream_exchanges` retains the exact bytes LiteLLM sent to and received from
the deterministic upstream for every provider round in that trial. `consumer`
records the SDK's own parsed event types, response ids, completed tool-call
ids, final response, and any exception the SDK itself raised.

All paths below are under `transcripts/085/`. Every JSONL file contains five
independent trials.

| Version | Evidence | Upstream calls | SDK result |
|---|---|---:|---|
| 1.102.1 | `1.102.1/trigger-duplicate-tool.jsonl` | 2 each | 2 completed tool calls, 5/5 |
| 1.102.1 | `1.102.1/trigger-sdk-crash-item-type.jsonl` | 2 each | `AssertionError`, 5/5 |
| 1.102.1 | `1.102.1/trigger-sdk-crash-partial-text.jsonl` | 2 each | `AssertionError`, 5/5 |
| 1.102.1 | `1.102.1/control-no-fault.jsonl` | 1 each | 1 completed tool call, 5/5 |
| 1.102.1 | `1.102.1/control-pre-first-chunk.jsonl` | 2 each | 1 completed tool call, 5/5 |
| 1.104.0 (main) | `1.104.0/trigger-duplicate-tool.jsonl` | 2 each | 2 completed tool calls, 5/5 |
| 1.104.0 (main) | `1.104.0/trigger-sdk-crash-item-type.jsonl` | 2 each | `AssertionError`, 5/5 |
| 1.104.0 (main) | `1.104.0/trigger-sdk-crash-partial-text.jsonl` | 2 each | `AssertionError`, 5/5 |
| 1.104.0 (main) | `1.104.0/control-no-fault.jsonl` | 1 each | 1 completed tool call, 5/5 |
| 1.104.0 (main) | `1.104.0/control-pre-first-chunk.jsonl` | 2 each | 1 completed tool call, 5/5 |

`trigger-duplicate-tool`'s response has two `response.created` events, and its
first `response.output_item.added` assigns `output_index` 0 to a completed
`function_call` with `call_id: call_primary`; its second assigns the same
index 0 to an unrelated completed `function_call` with `call_id: call_fallback`.
`trigger-sdk-crash-item-type` is identical except the second item is an
assistant text message, not a function call. `trigger-sdk-crash-partial-text`
reuses index 0 for a partial (never completed) text item followed by a
completed tool call, proving the collision is about the index namespace, not
about which item type or completion state started it.

The evidence is sanitized. Only `content-type` and `x-litellm-version` headers
are retained. `reproduce.py` rejects fixed local key strings and
credential-shaped body strings before writing a capture, and refuses to run
against a reused output directory.

## Control

Two controls change only whether the primary delivered any output item before
failing, holding the route, model names, tool definition, client SDK, and
fallback configuration fixed.

- **`control-no-fault`**: the primary completes its tool call successfully with
  no error, so no fallback is attempted. One upstream call, one lifecycle, the
  SDK's accumulator never sees a second `response.created`.
- **`control-pre-first-chunk`**: the primary fails immediately, before emitting
  any `response.output_item.added` event. LiteLLM correctly replays the
  original input (this is the intended `is_pre_first_chunk` path,
  `router.py:3274`). The fallback's own `output_item.added` at index 0 has
  nothing to collide with, because the SDK's snapshot list was still empty.
  The SDK completes cleanly with exactly one tool call.

Both controls pass 5/5 on both versions. Together they isolate the trigger:
LiteLLM's fallback replay is safe exactly when the primary delivered nothing,
and unsafe the moment it delivered anything, regardless of type or completion
state.

## Root cause

`Router._aresponses_streaming_iterator` (`litellm/router.py:3101`) wraps the
Responses-API streaming path added in
[PR #28215](https://github.com/BerriAI/litellm/pull/28215). On
`MidStreamFallbackError`, it checks only
`e.is_pre_first_chunk or not e.generated_content` (`router.py:3274`) to decide
whether the primary delivered anything. `generated_content` is accumulated
exclusively from `response.output_text.delta` events
(`litellm/responses/streaming_iterator.py:347`); a completed
`function_call` item is never added to it. So a stream that finished a tool
call but produced no text is treated identically to a stream that produced
nothing at all.

Once the fallback chain runs, the wrapper forwards the fallback iterator's
events verbatim (`router.py:3308`, `async for fallback_item in
fallback_response: ... yield fallback_item`). The fallback's own
`ResponsesAPIStreamingIterator` numbers its `output_index` and
`response.created`/`response.completed` identity independently, starting from
zero, exactly like the primary did. Nothing in the wrapper renumbers indexes,
merges the two lifecycles, or signals the client that a new namespace started.
The public stream is the literal concatenation of two independent,
zero-indexed provider streams.

The official `openai` SDK's `ResponseStreamState.accumulate_event`
(`_responses.py:325`) only resets its snapshot on the very first event
(`_create_initial_response` fires only when `self.__current_snapshot is
None`). Every later `response.created` is silently ignored by the accumulator.
Its per-event handlers then index into the stale snapshot by `output_index`
(`_responses.py:254`, `:294`) with a type assertion, which is exactly where a
reused index with a different item type raises `AssertionError`.

## Bug or not

- **Expected behavior is the intended contract**: LiteLLM's own PR #28215
  explicitly restates the chat-completions mid-stream fallback contract for the
  Responses API ("full parity with the chat-completions path") and documents
  injecting a continuation prompt "so the fallback model continues rather than
  restarts" when content was already generated. Preserving already-delivered
  content, not replaying it, is LiteLLM's own stated design.
- **Maintainer ruling checked**: merged PR
  [#40121](https://github.com/BerriAI/litellm/pull/40121) (2026-09-21, "stream
  one lifecycle across MCP auto-execute rounds") establishes that a single
  public Responses stream must expose one lifecycle to the client, for the
  adjacent MCP auto-execution path. It touches only
  `litellm/responses/mcp/mcp_streaming_iterator.py` and does not touch
  `router.py`'s fallback wrapper. The one-lifecycle invariant is the
  maintainers' own established fix pattern in this codebase; it has not been
  applied to mid-stream fallback.
- **Examples and tests checked**: the existing mock-only tests for this wrapper
  (`tests/router_unit_tests/test_router_aresponses_streaming_fallback.py`,
  added by #28215) cover only text-delta continuation. None constructs a
  primary stream that completes a `function_call` before failing, and none
  feeds the wrapper's output through the official SDK's accumulator. The gap is
  untested, not intentionally accepted.
- **Supported usage**: `router_settings.fallbacks` with the Responses API is a
  documented, first-party feature; the trigger is a plain retriable 5xx from
  the primary deployment, the single most common fallback trigger.
  `tool_choice: "required"` and a `strict` function tool are standard usage.
- **Boundary**: protocol compatibility, not disclosure. Valid client input and
  two independently valid provider streams become a public stream that either
  runs a side-effecting tool call twice or crashes the official SDK before
  a final response is available.
- **Maintainer fix**: track whether a completed output item (of any type,
  including `function_call`) was announced before falling back, and reuse the
  MCP-path pattern of composing internal rounds into one client-visible
  lifecycle with non-colliding indexes, so a delivered item is neither
  replayed nor its `output_index` reused.

Classification label: `bug`.

## Upstream status

Checked 2026-09-24 against release 1.102.1 and current main commit
[`1c8a0ff6`](https://github.com/BerriAI/litellm/commit/1c8a0ff6023b3eba6b01e955c49edcfa97bf0d22).

GitHub issue and pull request searches included open and closed results for
`MidStreamFallbackError`, `responses fallback duplicate tool`,
`responses fallback function_call twice`, `output_index collision`,
`responses.stream AssertionError fallback`, `aresponses_streaming_iterator`,
`second response.created fallback`, and `generated_content function_call
fallback`. Commit history on `litellm/router.py` and
`litellm/responses/streaming_iterator.py` since 2026-09-23 was also checked.

No exact report was found. The closest results are:

- [#29808](https://github.com/BerriAI/litellm/issues/29808), open, a
  non-streaming Vertex AI request's retry count is not honored before falling
  back. Unrelated: no streaming, no output-item duplication.
- [#41528](https://github.com/BerriAI/litellm/pull/41528), open, preserves
  provider headers across `MidStreamFallbackError`. Does not touch generated
  content tracking or output indexes.
- [#39703](https://github.com/BerriAI/litellm/issues/39703), open, the
  `/v1/messages` Responses bridge swallows a mid-stream failure instead of
  surfacing it. A different bridge, a different symptom (a hidden failure, not
  a replayed item).
- [#40121](https://github.com/BerriAI/litellm/pull/40121), merged 2026-09-21,
  the maintainer ruling this report relies on (see "Bug or not" above), fixing
  the same defect shape for the unrelated MCP auto-execution path.

Release notes for 1.102.1, the Responses-API and fallback documentation,
current source, the fallback wrapper's own tests, and commits touching the
iterator were checked. None reports or fixes a mid-stream fallback replaying an
already-delivered tool call or corrupting the SDK's output-index namespace.

Classification: `novel`, meaning no match in these searches. #40121 shows the
maintainers already accept and fix this exact defect shape for one code path;
this report extends the same standard to another code path they did not touch.

## Test

`responses_fallback_preserves_delivered_indexes` accepts a stream when no
`output_index` assigned by one internal lifecycle is reassigned to an unrelated
item by a later lifecycle. It is deliberately weaker than the existing
`responses_single_lifecycle` (bug 074): a `response.created` fallback replay
that reuses an index nothing was ever assigned to (the `is_pre_first_chunk`
control) is conformant here, because LiteLLM's own fallback feature legitimately
produces more than one `response.created` in a correct retry. What is never
conformant is an index collision between unrelated items, because the official
SDK's per-event accumulator dispatches on `output_index` without knowing a new
lifecycle started.

The conformance suite reads all five records for every mode and version,
checks the public route, target identity, and request shape, verifies upstream
call counts, tool-call counts, and SDK outcomes, and applies the invariant. A
mutated final trial and a malformed final JSONL record prove later evidence
cannot be skipped.

## Reproduction

Create a Python 3.12 environment with the pinned package. The retained
captures used Python 3.12.13; a reviewer can use any Python 3.12.x.

```sh
python3.12 -m venv /tmp/kairo-085-litellm-1102
/tmp/kairo-085-litellm-1102/bin/pip install -r transcripts/085/requirements-1.102.1.txt
/tmp/kairo-085-litellm-1102/bin/python -B transcripts/085/reproduce.py \
  --python /tmp/kairo-085-litellm-1102/bin/python \
  --expect-litellm 1.102.1 \
  --captured-at 2026-09-24 \
  --output-dir /tmp/kairo-085-output-1102

# current main (unreleased, install from a local checkout; no pinned package exists)
git clone https://github.com/BerriAI/litellm.git /tmp/litellm-085-main
python3.12 -m venv /tmp/kairo-085-litellm-main
/tmp/kairo-085-litellm-main/bin/pip install -e "/tmp/litellm-085-main[proxy]"
/tmp/kairo-085-litellm-main/bin/python -B transcripts/085/reproduce.py \
  --python /tmp/kairo-085-litellm-main/bin/python \
  --expect-litellm "$(/tmp/kairo-085-litellm-main/bin/python -c 'import importlib.metadata as m; print(m.version("litellm"))')" \
  --captured-at 2026-09-24 \
  --output-dir /tmp/kairo-085-output-main

python3 -B -m unittest transcripts/085/test_reproduce.py
python3 -B -O -m unittest transcripts/085/test_reproduce.py
cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
python3 tools/update-readme-counts.py --check
```

Use fresh output directories. Existing evidence under `transcripts/085/` is
never overwritten; `reproduce.py` refuses to run against a directory that
already exists. The runner uses loopback ports only, installs no software
beyond the pinned package, reads no environment credentials, and makes no
external network calls.
