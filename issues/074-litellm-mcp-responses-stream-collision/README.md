# 074, LiteLLM MCP auto-execution splices two Responses lifecycles into one stream

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). No exact
  upstream ticket found on 2026-09-07. Related reports are listed below.
- **Tool under test**: LiteLLM 1.99.0 and current release
  [1.100.0](https://github.com/BerriAI/litellm/releases/tag/v1.100.0), OpenAI
  Python SDK 2.54.0, MCP 1.29.1.
- **Reproduced**: 2026-09-07 on macOS arm64 through the real LiteLLM proxy
  `POST /v1/responses` route, a real local stdio MCP server, and a deterministic
  OpenAI Responses capture upstream. No provider credential is needed.

## What breaks

A developer follows LiteLLM's supported MCP gateway workflow with
`stream: true` and `require_approval: "never"`. The first model round requests
the MCP tool, LiteLLM executes it, and a second model round produces the final
answer. LiteLLM writes both complete upstream Responses lifecycles into the one
client stream. The second round starts a new `response.created` event and
restarts `output_index` at zero after the first round already emitted
`response.completed`.

The official OpenAI Python SDK's `client.responses.stream()` accumulator keeps
one response snapshot per stream. When the second round's text delta addresses
index zero, that slot still contains the first round's function call. SDK 2.54.0
therefore raises `AssertionError` at
[`_responses.py:254`](https://github.com/openai/openai-python/blob/v2.54.0/src/openai/lib/streaming/responses/_responses.py#L247-L257).
The MCP tool ran, and the provider returned `FINAL_FROM_ROUND_2`, but the caller
never receives a final SDK response.

```text
OpenAI SDK -> LiteLLM /v1/responses -> provider round 1: function_call, completed
                                      -> local MCP echo executes once
                                      -> provider round 2: final text, completed
           <- one SSE body containing both lifecycles and two index-zero items
OpenAI SDK -> AssertionError before final response
```

Measured impact is 5/5 SDK failures on each tested LiteLLM version. This occurs
when an auto-executed MCP call reaches a follow-up text round and the caller uses
the official accumulating stream helper. Production frequency was not measured.

## Wire evidence

Each JSONL line is one complete public exchange. `client_request.body_raw` and
`client_response.body_raw` retain the exact UTF-8 bodies seen by the SDK relay.
Every entry in `upstream_exchanges` retains the exact request and response bodies
for one provider round. `mcp_calls` records the real local tool invocation, and
`consumer` records parsed events, response ids, final response, and SDK error.

All paths below are under `transcripts/074/`. Every JSONL file contains five
independent trials.

| Version | Evidence | Provider rounds | MCP calls | SDK result |
|---|---|---:|---:|---|
| 1.99.0 | `1.99.0/trigger.jsonl` | 2 each | 1 each | `AssertionError`, 5/5 |
| 1.99.0 | `1.99.0/approval.jsonl` | 1 each | 0 | final function call, 5/5 |
| 1.99.0 | `1.99.0/no-tool.jsonl` | 1 each | 0 | final text, 5/5 |
| 1.100.0 | `1.100.0/trigger.jsonl` | 2 each | 1 each | `AssertionError`, 5/5 |
| 1.100.0 | `1.100.0/approval.jsonl` | 1 each | 0 | final function call, 5/5 |
| 1.100.0 | `1.100.0/no-tool.jsonl` | 1 each | 0 | final text, 5/5 |

The trigger response has two `response.created` and two `response.completed`
events with two response ids. Its first `response.output_item.added` assigns
index zero to `fc_round_1`; its second assigns index zero to `msg_round_2`.
Both provider streams are independently well formed and restart their namespaces
because they are distinct upstream responses. The defect is exposing both
namespaces unchanged as one public response.

The evidence is sanitized. Only `content-type` and `x-litellm-version` headers
are retained. The runner rejects fixed local key strings, credential-shaped body
strings, reused output directories, and unexpected package versions before
writing a completed capture.

## Control

The closest control changes only the MCP tool's `require_approval` value from
`"never"` to `"always"`. LiteLLM returns the first function call without executing
it or starting a follow-up round. The stream has one created/completed pair and
the same OpenAI SDK helper succeeds 5/5 on both versions.

The no-tool control exercises the same SDK, public route, model deployment,
capture relay, and provider stream. It returns `CONTROL_TEXT` through one valid
lifecycle 5/5 on both versions. Together the controls isolate LiteLLM's MCP
auto-execution composition rather than the endpoint, SDK setup, input, provider
framing, or capture relay.

## Root cause

`MCPEnhancedStreamingIterator._create_follow_up_iterator` creates a fresh
`aresponses()` iterator for every internal round and installs it directly as the
same public stream's base iterator. It also clears `_cached_response_id`, so the
new iterator's response identity is exposed. See
[`mcp_streaming_iterator.py:790-821`](https://github.com/BerriAI/litellm/blob/v1.100.0/litellm/responses/mcp/mcp_streaming_iterator.py#L790-L821).

Each fresh `LiteLLMCompletionStreamingIterator` initializes its created,
completed, output-index, and sequence state again. See
[`streaming_iterator.py:77-120`](https://github.com/BerriAI/litellm/blob/v1.100.0/litellm/responses/litellm_completion_transformation/streaming_iterator.py#L77-L120).
The outer MCP iterator forwards these events without composing their namespaces
into one client response.

## Bug or not

- **Expected behavior is the protocol contract:** one Responses stream describes
  one response and ends in one terminal event. OpenAI's official stream helper
  embodies that contract with one current snapshot. LiteLLM advertises the
  [`/responses` endpoint](https://github.com/BerriAI/litellm/blob/v1.100.0/README.md#L80-L90).
- **Examples checked:** LiteLLM's MCP cookbook uses the OpenAI SDK, proxy MCP
  tool, `require_approval: "never"`, `stream: true`, and `tool_choice: "required"`
  in the same request shape. See
  [`mcp_with_litellm_proxy.py:7-37`](https://github.com/BerriAI/litellm/blob/v1.100.0/cookbook/litellm_proxy_server/mcp/mcp_with_litellm_proxy.py#L7-L37).
- **Tests checked:** LiteLLM's internal iterator test deliberately expects three
  internal completed rounds. It does not pass the resulting public bytes through
  an OpenAI SDK accumulator. See
  [`test_mcp_streaming_iterator.py:104-141`](https://github.com/BerriAI/litellm/blob/v1.100.0/tests/test_litellm/responses/mcp/test_mcp_streaming_iterator.py#L104-L141).
  Internal multi-round execution is intended; exposing independent lifecycle and
  index namespaces to one client stream is the bug.
- **UI checked:** current main's agent builder generates proxy MCP tools with
  `require_approval: "never"`. See
  [`AgentBuilderView.tsx:161-172`](https://github.com/BerriAI/litellm/blob/eeb7732fc11fd47762ca84cc3fb7cc74235d7097/ui/litellm-dashboard/src/app/%28dashboard%29/playground/components/chat_ui/AgentBuilderView.tsx#L161-L172).
- **Maintainer ruling checked:** merged PR
  [#33025](https://github.com/BerriAI/litellm/pull/33025) deliberately exposes the
  final upstream response id so continuation uses the right stored response. It
  does not rule that multiple terminal lifecycles and colliding public indexes are
  valid, and it does not test the official accumulator.
- **Supported usage:** the trigger is the documented proxy MCP configuration with
  authentication enabled, recommended `require_approval: "never"`, streaming, and
  the official OpenAI SDK. The upstream is deterministic because the claim is
  about LiteLLM's composition of valid provider bytes.
- **Boundary:** protocol compatibility, not disclosure. Valid public input and two
  valid internal provider responses become a stream the official SDK cannot
  consume, halting the agent before its answer.
- **Maintainer fix:** compose internal MCP rounds into one public response
  lifecycle with one client identity and globally non-colliding output indexes,
  while retaining the final upstream id separately for continuation.

Classification label: `bug`.

## Upstream status

Checked 2026-09-07 against release 1.100.0 and main commit
[`eeb7732`](https://github.com/BerriAI/litellm/commit/eeb7732fc11fd47762ca84cc3fb7cc74235d7097).
GitHub issue and pull request searches included open and closed results for
`MCPEnhancedStreamingIterator`, `AssertionError responses.stream MCP`,
`response.created response.completed MCP`, `output_index MCP Responses`,
`duplicate lifecycle`, and `MCP response id`.

No exact report was found. The closest results are:

- [#31910](https://github.com/BerriAI/litellm/issues/31910), open, Chat
  Completions exposes an intermediate `finish_reason` and breaks chat UIs.
- [#37358](https://github.com/BerriAI/litellm/issues/37358), open, chained MCP
  rounds are missing from spend logs.
- [#33024](https://github.com/BerriAI/litellm/issues/33024), closed by #33025,
  the final response id previously pointed continuation at an interim round.
- [#32561](https://github.com/BerriAI/litellm/issues/32561), closed, an initial
  provider failure produced MCP discovery events without `response.created`.
- [#27442](https://github.com/BerriAI/litellm/issues/27442), closed, general
  Responses event omissions broke strict clients on older versions.

Release notes for 1.100.0, the MCP documentation and cookbook, current source,
the relevant iterator tests, and commits touching the iterator were checked. None
reports or fixes the successful auto-execution path producing multiple complete
lifecycles with reused indexes and crashing `client.responses.stream()`.

Classification: `novel`, meaning no match in these searches. The related issues
show that intermediate MCP stream framing is an active problem family, but their
routes, triggers, wire defects, and consumer failures differ.

## Test

`responses_single_lifecycle` accepts a stream only when it contains one
`response.created`, one matching and terminal `response.completed`, and no
`response.output_item.added` index assigned to unrelated item ids. It fails closed
on empty, malformed, incomplete, identity-changing, post-terminal, and colliding
streams.

The conformance suite reads all five records for every mode and version, checks
public route and target identity, parses the raw SDK request, verifies provider
round and MCP call counts, confirms the SDK outcome, and applies the invariant.
A mutation of trial five and a malformed final JSONL record prove later evidence
cannot be skipped.

## Reproduction

Create separate environments so both package versions stay pinned:

```sh
python3 -m venv /tmp/kairo-074-litellm-199
/tmp/kairo-074-litellm-199/bin/pip install -r transcripts/074/requirements-1.99.txt
/tmp/kairo-074-litellm-199/bin/python -B transcripts/074/reproduce.py \
  --python /tmp/kairo-074-litellm-199/bin/python \
  --expect-litellm 1.99.0 \
  --output-dir /tmp/kairo-074-output-199

python3 -m venv /tmp/kairo-074-litellm-1100
/tmp/kairo-074-litellm-1100/bin/pip install -r transcripts/074/requirements-1.100.txt
/tmp/kairo-074-litellm-1100/bin/python -B transcripts/074/reproduce.py \
  --python /tmp/kairo-074-litellm-1100/bin/python \
  --expect-litellm 1.100.0 \
  --output-dir /tmp/kairo-074-output-1100

python3 -B -m unittest transcripts/074/test_reproduce.py
python3 -B -O -m unittest transcripts/074/test_reproduce.py
cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
python3 tools/update-readme-counts.py --check
```

Use fresh output directories. Existing evidence is never overwritten. The runner
uses loopback ports only, installs no software, reads no environment credentials,
and makes no external network calls.
