# kairo

**A conformance suite and open dataset for LLM tool-call translation.**

Every LLM gateway, proxy, and OpenAI-compatible server translates between wire
dialects: OpenAI Chat Completions, Anthropic Messages, and the OpenAI Responses
API. Translation is lossy by construction, because each dialect carries fields
the others do not. The question is what a gateway does with the fields it
cannot carry. The correct answers are to map them, to refuse the request, or to
report the loss. The common answer is to drop the field and return HTTP 200.

Tool calls are where this matters. An agent loop does not read the model's
text to decide what to do next. It reads the stop reason, the tool-call id, and
the arguments. When a gateway relabels `tool_use` as `end_turn`, rewrites an id
without an inverse, or stringifies an image inside a tool result, the loop
halts, races, or goes blind, and the model gets blamed.

kairo reproduces these failures on the wire, freezes each one as a
deterministic replay test, and scores any translation layer against the
resulting invariants. Every finding is backed by recorded bytes, a control that
succeeded on the same input, and an N of N reproduction count. The suite runs
offline with no provider keys.

## Status

<!-- kairo-counts:start -->
| Metric | Value |
|---|---|
| Reproduced issue folders | 50 |
| Gateways under test | LiteLLM, NVIDIA Switchyard, Bifrost, GoModel, AxonHub, and any-llm |
| Harness tests | 147 (126 conformance checks against recorded transcripts, 21 unit) |
<!-- kairo-counts:end -->

The 50 folders cover reproduced findings, multi-defect reports, and honest
negative results. Versions and reproduction outcomes are recorded per finding
in [`issues/SCOREBOARD.md`](issues/SCOREBOARD.md), including cited bugs that did
not reproduce.
One finding is filed upstream as
[NVIDIA-NeMo/Switchyard#380](https://github.com/NVIDIA-NeMo/Switchyard/issues/380).

## Findings

Independent gateways violate the same small set of invariants, and a checker
written against one gateway's transcript catches the same defect in the others
without modification. That is the central result. The table groups every reproduced defect by the invariant it
violates. Each number links to the folder with the writeup, the bytes, and the
reproduction commands.

| Invariant | LiteLLM | Switchyard | Bifrost | GoModel | AxonHub | any-llm |
|---|---|---|---|---|---|---|
| Terminal reason survives translation (`tool_use`, `content_filter`, `max_tokens`, refusal) | [001](issues/001-anthropic-stream-toolcall-translation), [002](issues/002-litellm-ollama-toolcall-loss) | [010](issues/010-switchyard-content-filter-and-reorder) | [030](issues/030-bifrost-anthropic-stream-stop-reason), [034](issues/034-bifrost-erases-content-filter), [035](issues/035-bifrost-erases-truncation), [036](issues/036-bifrost-drops-refusal-content) | | | |
| Request constraints survive (`disable_parallel_tool_use`, `stop_sequences`, `tools[].strict`, `output_format`) | [017](issues/017-parallel-tool-flag-dropped), [041](issues/041-litellm-drops-stop-sequences), [064](issues/064-litellm-drops-tool-strict) | [006](issues/006-switchyard-crossformat-losses), [017](issues/017-parallel-tool-flag-dropped), [040](issues/040-switchyard-drops-output-format), [065](issues/065-switchyard-responses-instruction-loss), [066](issues/066-switchyard-drops-tool-strict) | [031](issues/031-bifrost-drops-parallel-tool-flag), [032](issues/032-bifrost-drops-stop-sequences), [072](issues/072-bifrost-anthropic-tool-choice-any-leak) | [042](issues/042-gomodel-drops-output-format), [043](issues/043-gomodel-drops-parallel-tool-flag) | [051](issues/051-axonhub-drops-output-format) | [058](issues/058-any-llm-drops-parallel-tool-flag), [062](issues/062-any-llm-empty-schema-shell) |
| Content blocks survive (refusal, `is_error`, image and document blocks in tool results and user turns) | [006](issues/006-switchyard-crossformat-losses), [007](issues/007-switchyard-toolresult-multimodal-stringified), [018](issues/018-user-document-dropped), [067](issues/067-litellm-drops-refusal-content) | [006](issues/006-switchyard-crossformat-losses), [007](issues/007-switchyard-toolresult-multimodal-stringified), [018](issues/018-user-document-dropped), [068](issues/068-switchyard-drops-refusal-content), [069](issues/069-switchyard-responses-refusal) | | | | [059](issues/059-any-llm-drops-is-error), [060](issues/060-any-llm-drops-toolresult-image), [061](issues/061-any-llm-drops-toolresult-document) |
| Assistant history survives replay (`thinking` blocks and signatures) | [016](issues/016-thinking-history-lost) (leaked as visible text) | [016](issues/016-thinking-history-lost) (dropped) | [033](issues/033-bifrost-drops-thinking-history) | | | [057](issues/057-any-llm-drops-thinking-history) |
| Tool-call ids round-trip | [004](issues/004-gemini-thought-signature) | [005](issues/005-switchyard-toolid-sanitizer) | [037](issues/037-bifrost-toolid-not-restored) | | | |
| Nothing is invented (empty text blocks, phantom message items, `cache_control`) | [001](issues/001-anthropic-stream-toolcall-translation), [009](issues/009-litellm-responses-phantom-message) | [019](issues/019-switchyard-invents-prompt-cache), [045](issues/045-switchyard-empty-text-before-tooluse), [068](issues/068-switchyard-drops-refusal-content) | | | | |
| Malformed input fails closed | [008](issues/008-litellm-messages-indexerror-crash) | | | | | |
| Client credentials stay client-side | [020](issues/020-litellm-client-api-key), [024](issues/024-litellm-health-extra-headers), [026](issues/026-litellm-extra-headers-org), [028](issues/028-litellm-gemini-passthrough-upload-url), [071](issues/071-litellm-model-info-api-base-leak) | [023](issues/023-switchyard-forwards-org-api-key), [025](issues/025-switchyard-transport-query-key), [027](issues/027-switchyard-forwards-x-goog-api-key), [063](issues/063-switchyard-redirect-follows-x-api-key) | | | | |

The `disable_parallel_tool_use` flag is dropped by five of the six gateways.
The Anthropic `{"type": "auto", "disable_parallel_tool_use": true}` object
becomes the bare string `"auto"`, and no `parallel_tool_calls: false` appears
on the OpenAI-shaped side. The
[017 checker](crates/harness/src/checks.rs) caught Switchyard and LiteLLM
first and then Bifrost, GoModel, and any-llm unchanged.

Two gateways route Anthropic `/v1/messages` through the OpenAI Responses API
rather than Chat Completions, even when the configured backend is `openai/*`.
Field names change again on that hop (`messages` becomes `input`, `system`
becomes `instructions`, `max_tokens` becomes `max_output_tokens`). A probe
corpus written against Chat Completions spellings scores those fields as
dropped when they were carried. Twelve cells in an early sweep were false
drops for that reason, and the corpus now checks both spellings.

The same field fails in different ways across gateways, and the failure mode
matters. Replayed `thinking` blocks are dropped by Switchyard, Bifrost, and
any-llm, which breaks reasoning continuity and prompt caching. LiteLLM instead
forwards them as visible `output_text`, which puts private reasoning into the
model's visible context. An image inside a `tool_result` is JSON-dumped into a
text string by Switchyard, so the model receives literal base64, and is deleted
outright by LiteLLM.

Honest negatives are kept as data. Bifrost's multimodal handling and its
handling of client credentials are correct where both incumbents fail.
Switchyard's streaming tool-call re-encoder reassembles split argument deltas
correctly. Several cited upstream tickets are patched on current releases and
are recorded as non-reproductions. Issue 030 is a regression of a Bifrost bug
fixed in v1.5.4, which is the argument for a permanent suite rather than a
one-time audit.

## Method

Every accepted finding has three legs.

1. **Wire evidence.** The bytes as sent and received, under
   [`transcripts/`](transcripts). Raw SSE or JSON. Screenshots and paraphrases
   are not evidence.
2. **A control that works.** The same input succeeding somewhere: the model
   called directly, another route on the same gateway, or another version.
   The control isolates the translation layer as the cause and rules out the
   model and the prompt.
3. **Determinism.** N of N reproductions, with the trigger narrowed to the
   exact field, length, or chunk shape. A failure that reproduces only
   sometimes is a lead and needs narrowing before it becomes a finding.

A finding becomes a checker in
[`crates/harness/src/checks.rs`](crates/harness/src/checks.rs) that encodes the
invariant rather than the bug:

```rust
pub enum Verdict {
    Conformant,
    Violation(String),
}

/// If a streamed Anthropic response contains a `tool_use` block, the
/// terminal `stop_reason` MUST be `tool_use`.
pub fn anthropic_toolcall_stop_reason(sse: &str) -> Verdict
```

A test in
[`crates/harness/tests/conformance.rs`](crates/harness/tests/conformance.rs)
asserts the verdict against the recorded bytes. Run against a buggy gateway's
transcript, the checker returns the violation. Run against a correct
implementation, it returns conformance. The same checker scores both
directions, which is what lets the suite grade a target instead of arguing
about it. A `Violation` assertion is a frozen bug: the day a gateway stops
violating the invariant, the test flips and says so.

## Architecture

```
                 client dialect                 backend dialect
  ┌────────────┐  Anthropic Messages  ┌──────────┐  Chat / Responses  ┌───────────────┐
  │ agent or   │ ───────────────────> │ gateway  │ ─────────────────> │ capture mock  │
  │ curl       │ <─────────────────── │ under    │ <───────────────── │ or live model │
  └────────────┘   SSE / JSON         │ test     │   canned or real   └───────────────┘
        │                             └──────────┘                           │
        │  response bytes                                  forwarded request  │
        └────────────────> transcripts/NNN/ <────────────────────────────────┘
                                   │
                                   v
                     crates/harness (checkers + conformance tests)
```

Two capture rigs cover the findings.

The **offline capture rig** points the gateway's backend at
[`tools/mock_upstream.py`](tools/mock_upstream.py), which appends every
forwarded request body to a JSONL file and replies with a canned SSE stream or
JSON body. This exposes encode-side losses, meaning fields that were in the
client request and are absent from what reached the backend. It needs no keys
and is fully deterministic. Most request-constraint, tool-result, history, and
credential findings were captured this way.

The **live capture rig** runs the gateway against a real backend (Ollama,
Gemini, Kimi, Anthropic, OpenAI) and records both directions. This exposes
decode-side failures, meaning stop reasons, ids, and content the gateway
produced for the client, and it measures what a loss costs a real run.

The harness is a single Rust crate with no network access. Checkers operate on
raw SSE bodies, response JSON, and capture JSONL. The workspace forbids unsafe
code and denies all clippy lints at the pedantic level. CI runs the README
counter check, `rustfmt`, `clippy`, unit, conformance, and doc tests, and
`rustdoc` with warnings denied.

```
crates/harness/src/checks.rs        invariant checkers, one per defect class
crates/harness/tests/conformance.rs one test per recorded transcript
issues/NNN-slug/README.md           writeup: what breaks, evidence, control, invariants, repro
issues/SCOREBOARD.md                every finding, version, ticket, and result
issues/MATRIX.md                    field-preservation matrix from the sweep rig
issues/TARGETS.md                   unclaimed upstream tickets to reproduce
transcripts/NNN/                    recorded bytes per finding
tools/mock_upstream.py              offline capture backend
tools/capture_server*.py            request recorders for specific dialects
tools/sweep/                        rectangular gateway x probe sweep
tools/update-readme-counts.py       regenerates the Status block; CI fails if stale
```

## Coverage

| Axis | Covered |
|---|---|
| Client dialects | Anthropic Messages, OpenAI Chat Completions, OpenAI Responses |
| Backend dialects | OpenAI Chat Completions, OpenAI Responses, Anthropic Messages, Gemini, Ollama |
| Gateways | LiteLLM, NVIDIA Switchyard, Bifrost, GoModel, AxonHub, any-llm |
| Modes | streaming and non-streaming, per route |
| Surfaces | stop and finish reasons, tool-call ids, argument assembly, request constraints, multimodal tool results, replayed history, invented fields, error handling, credential handling |

Versions are pinned in each writeup. Findings are stated against the exact
release or commit they were captured on, and the scoreboard records whether a
cited bug reproduces on the current release.

## Quick start

Replay the suite. No keys are needed because the tests read recorded bytes.

```bash
cargo test
```

Reproduce a finding offline. This is issue 017 against Switchyard: the
forwarded `tool_choice` arrives as the string `"auto"` with no
`parallel_tool_calls` field.

```bash
python tools/capture_server.py $PWD/transcripts/016/cap-parallel.jsonl &
tools/switchyard/target/release/switchyard-server --config tools/switchyard-capture.toml --port 9000 &
curl -s localhost:9000/v1/messages -H 'anthropic-version: 2023-06-01' \
  -d @transcripts/016/req-parallel.json
```

Reproduce a finding live. Keys are used for capture only and are never
committed.

```bash
cp .env.example .env
# add provider keys, then follow the repro block in any issues/NNN/README.md
```

## Reading a finding

Each folder under `issues/` follows [`issues/TEMPLATE.md`](issues/TEMPLATE.md)
and states, in order: the upstream ticket and its state on the reproduction
date, the tool and exact version under test, the reproduction date and
environment, what breaks and which agent loops it hurts, the wire evidence
with file names, the control matrix, the root cause if it was pinned to a
source line, and the invariants the bug implies. A writeup says what was
checked and what was not. Where the reproduction path differed from the
upstream reporter's configuration, the writeup says so and explains what that
difference does to the claim.

## The denominator

Issue folders answer "does gateway X drop field Y". The sweep rig under
[`tools/sweep/`](tools/sweep) answers the question underneath: of every field a
cross-format gateway has to carry, how many survive, on each gateway, measured
the same way. It runs every gateway against every probe, repeats non-clean
cells to N runs, and writes [`issues/MATRIX.md`](issues/MATRIX.md) with a
preservation rate per gateway and a legend that separates a dropped field from
a field with no equivalent in the target format and from a gateway that could
not be started. An absent gateway and a clean gateway never look the same in
the results.

The sweep produces leads and frozen bytes. It does not write issue folders.
Every folder remains a hand-verified claim with a control and a determinism
count, one bug per pull request.

## Reporting a failure

If a tool call broke behind a gateway, the transcript is the contribution. No
diagnosis is required.

Install the `/kairo-report` command into Claude Code once:

```bash
mkdir -p ~/.claude/commands && curl -fsSL https://raw.githubusercontent.com/Atharva-Kanherkar/kairo/main/agent-commands/claude-code/kairo-report.md -o ~/.claude/commands/kairo-report.md
```

The next time a tool call fails, run `/kairo-report` in that session. The
agent gathers the evidence, redacts secrets, shows the report, and files it
after confirmation. The manual route is a
[tool-call failure report](https://github.com/Atharva-Kanherkar/kairo/issues/new?template=tool-call-failure.md).

## Contributing

New reproductions are the highest-value contribution, and proving a bug is
real is sufficient. A fix is not required. Non-reproductions of cited bugs are
recorded as data. The method, the pull-request checklist, and the style rules
are in [CONTRIBUTING.md](CONTRIBUTING.md). Unclaimed targets, including
vLLM, SGLang, Ollama, and claude-code-router tickets, are listed in
[`issues/TARGETS.md`](issues/TARGETS.md).

## Scope and non-goals

kairo tests translation layers. It does not benchmark model quality, and a
model that declines to call a tool is not a finding. It does not ship fixes to
the gateways it tests; findings are filed upstream and linked from the
writeup. It hunts silent failures first: a gateway that returns a clean 4xx
for an unsupported field is recorded as loud and correct, and the checker
grammar distinguishes a dropped field from a field with no equivalent in the
target dialect.

## Roadmap

The end goal is a router that lets any coding agent run on any model with
tool calls that survive translation. The router will be built on this dataset
and scored by this suite, and it is not started. Nearer work is widening the
gateway column (vLLM, SGLang, Ollama's OpenAI compatibility layer,
claude-code-router), completing the sweep across all six gateways on current
releases, and filing the remaining unfiled findings upstream.

## License

Apache-2.0. See [LICENSE](LICENSE).
