# 082, Bifrost agent mode drops one result when the same tool runs twice

- **Upstream**: [maximhq/bifrost](https://github.com/maximhq/bifrost), no
  matching issue or pull request found on 2026-09-21
- **Tool under test**: Bifrost `dev` commit
  [`1a17949cd80234de0ceb3018e941854f38b3f7a8`](https://github.com/maximhq/bifrost/commit/1a17949cd80234de0ceb3018e941854f38b3f7a8),
  core `1.9.1`, locally built HTTP transport reporting
  `1.9.1-1a17949c`
- **Reproduced**: 2026-09-21 on macOS arm64 with Go 1.27.1, public
  `POST /v1/chat/completions`, OpenAI Chat Completions on both sides,
  deterministic local OpenAI-compatible upstream, streamable HTTP MCP,
  model identifier `mockoai/mimo-v2.5`, non-streaming, agent mode enabled

## What breaks

In a mixed agent-mode turn, a model can ask Bifrost to run the same
auto-executable tool twice with different tool-call IDs and then ask for a
third tool that requires approval. Bifrost executes both allowed calls, but
returns only one result in the client-visible JSON summary. It keys that
summary by tool name, so the later parallel write replaces the earlier one.

The affected user is an application that follows Bifrost's documented mixed
agent-mode workflow: parse the response content to learn which side effects
already ran, decide the pending manual tool call, and continue the
conversation. Repeated calls to one tool are ordinary for batch workflows,
such as charging two invoices, writing two files, or fetching two records.
The application cannot distinguish an omitted result from a side effect that
never ran.

The deterministic consumer in this reproduction requests any side effect
whose result marker is absent from the conversation. The missing result makes
it execute the side-effecting tool a second time 5/5. The distinct-tool-name
control executes the same two initial side effects and duplicates neither,
0/5.

## Wire evidence

All captures are sanitized under [`transcripts/082/`](../../transcripts/082/).
The full matrix is in
[`results.json`](../../transcripts/082/results.json).

| Cell | Initial model calls | MCP executions | Results returned | Consumer duplicate |
|---|---:|---:|---:|---:|
| violation | `charge(alpha)`, `charge(beta)`, pending `review` | 2 per run, 5/5 | 1 per run, 5/5 | 1 per run, 5/5 |
| distinct-name control | `charge(alpha)`, `credit(beta)`, pending `review` | 2 per run, 5/5 | 2 per run, 5/5 | 0 per run, 5/5 |
| single-call control | `charge(alpha)`, pending `review` | 1 per run, 5/5 | 1 per run, 5/5 | not applicable |

First-run violation evidence:

- [`violation-client-request.http`](../../transcripts/082/violation-client-request.http):
  the raw request to Bifrost's public endpoint
- [`upstream-003-response.http`](../../transcripts/082/upstream/upstream-003-response.http):
  the raw upstream response with distinct IDs for the two `charge` calls
- [`mcp-004-request.http`](../../transcripts/082/mcp/mcp-004-request.http) and
  [`mcp-005-request.http`](../../transcripts/082/mcp/mcp-005-request.http):
  raw MCP calls proving both side effects reached the real MCP client path
- [`mcp-004-response.http`](../../transcripts/082/mcp/mcp-004-response.http)
  and
  [`mcp-005-response.http`](../../transcripts/082/mcp/mcp-005-response.http):
  raw, distinct MCP results
- [`violation-client-response.http`](../../transcripts/082/violation-client-response.http):
  the raw Bifrost response containing only one result marker
- [`violation-executions.json`](../../transcripts/082/violation-executions.json):
  normalized index derived from the raw MCP execution log, retaining both
  tool-call IDs and result markers
- [`violation-consumer-request.http`](../../transcripts/082/violation-consumer-request.http)
  and
  [`violation-consumer-response.http`](../../transcripts/082/violation-consumer-response.http):
  the identical next-turn policy driven through Bifrost

The corresponding first-run controls are
[`control_distinct-client-response.http`](../../transcripts/082/control_distinct-client-response.http)
and
[`control_single-client-response.http`](../../transcripts/082/control_single-client-response.http).
Raw bytes for every run remain in `runs/`, `upstream/`, and `mcp/`.

No provider credential was used. The configured value is synthetic and exists
only in a temporary runtime directory. Captured authorization is replaced
inline with `<SYNTHETIC_PROVIDER_KEY>`.

## Root cause

The Chat adapter first creates the correct `tool_call_id -> tool name` lookup
at
[`core/mcp/agentadaptors.go:228-234`](https://github.com/maximhq/bifrost/blob/1a17949cd80234de0ceb3018e941854f38b3f7a8/core/mcp/agentadaptors.go#L228-L234).
For each executed result it resolves the original tool-call ID, but then
stores the result as `toolResultsMap[toolName]` at
[`agentadaptors.go:240-274`](https://github.com/maximhq/bifrost/blob/1a17949cd80234de0ceb3018e941854f38b3f7a8/core/mcp/agentadaptors.go#L240-L274).
Two distinct calls to one tool therefore occupy one map key. Because execution
is parallel, which result survives is nondeterministic.

The same implementation shape is present in the Responses adapter at
[`agentadaptors.go:474-520`](https://github.com/maximhq/bifrost/blob/1a17949cd80234de0ceb3018e941854f38b3f7a8/core/mcp/agentadaptors.go#L474-L520).
The source-level probe in
[`source_probe_test.go`](../../transcripts/082/source_probe_test.go) fails for
both internal adapters in five separate processes. The exact public-route
claim in this issue is limited to Chat Completions, which is exercised through
the built HTTP gateway.

One-sentence fix: serialize executed results by tool-call ID, retaining the
tool name and output for each call, instead of placing every result for one
tool name in a single map entry.

## Gate 1: correctness

### Exact reproduction

```bash
git clone https://github.com/maximhq/bifrost.git /tmp/bifrost-082
cd /tmp/bifrost-082
git checkout 1a17949cd80234de0ceb3018e941854f38b3f7a8
make setup-workspace
mkdir -p transports/bifrost-http/ui
printf '<!doctype html><title>embed stub</title>\n' > transports/bifrost-http/ui/index.html
(cd transports/bifrost-http && go build \
  -ldflags '-X main.Version=1.9.1-1a17949c' \
  -o /tmp/bifrost-082-http .)

cd /path/to/kairo
BIFROST_SOURCE=/tmp/bifrost-082 \
BIFROST_BIN=/tmp/bifrost-082-http \
python3 transcripts/082/reproduce.py
```

The transport embeds a React build that is not committed in the upstream
source checkout. The one-line HTML file only satisfies Go's compile-time
embed requirement and is outside the request path. The gateway, provider
adapter, agent loop, MCP client, streamable HTTP connection, and public route
are real Bifrost code from the pinned checkout.

The runner checks the full commit, core version, relevant source cleanliness,
runtime version, and binary path. It creates an enabled temporary SQLite
config store, starts the two capture servers, drives the public route, and
writes reviewer-owned evidence to a fresh temporary directory by default.

Expected behavior: every distinct auto-executed tool call is represented in
the mixed-turn result summary before the application handles pending manual
calls.

Observed behavior: both same-name calls execute, but exactly one result is
returned. The surviving marker changes with parallel completion order.

Reproduction rate: 5/5 violation runs lost one of two results. Both controls
were conformant 5/5.

Smallest trigger: at least two auto-executable calls with distinct tool-call
IDs but the same tool name, plus one non-auto-executable call in the same
model turn so Bifrost returns its mixed-turn summary.

### Source-level falsifier

```bash
cp /path/to/kairo/transcripts/082/source_probe_test.go \
  /tmp/bifrost-082/core/mcp/zz_h1_probe_test.go
cd /tmp/bifrost-082/core
go test ./mcp -run TestH1Invariant -count=1 -v
```

The probe is expected to exit nonzero on affected source. Five separate runs
lost one result in both internal adapters. The recorded summary is
[`source-probe-5runs.txt`](../../transcripts/082/source-probe-5runs.txt).

### Control

The primary control changes only the second tool's name from `charge` to
`credit`. It preserves the model turn shape, two side effects, distinct IDs,
arguments, mixed auto/manual split, public route, upstream, MCP server, and
consumer. Both results are returned 5/5 and the consumer makes no duplicate
call 5/5. The single-call control removes only the repeated call and returns
its sole result 5/5.

The controls attribute the failure to the tool-name collision in Bifrost's
summary rather than to MCP execution, the fake provider, malformed input, or
the consumer.

### False-positive checks

- [x] Tested the exact same-name result-loss claim, not a nearby agent-loop symptom.
- [x] Pinned and ran current `dev` commit `1a17949c` through its public HTTP route.
- [x] Used the required enabled SQLite config store and documented agent-mode fields.
- [x] Used valid, distinct tool-call IDs and valid OpenAI Chat Completions bytes.
- [x] Removed model nondeterminism with a deterministic local upstream.
- [x] Confirmed the source-level formatter fails independently of the network mocks.
- [x] Confirmed both side effects at raw MCP request and response boundaries.
- [x] Sanitized the captures and used no provider credential.

The identity of the surviving result is nondeterministic because the two calls
run concurrently. The loss itself is deterministic, 5/5. The claim does not
depend on which result wins.

## Gate 2: usefulness

### Who gets bitten

- **Affected user**: an application or agent host using Bifrost MCP agent mode
  with approval-gated tools
- **Real workflow**: the model batches repeated calls to one auto-executable
  side-effecting tool, Bifrost runs them, and the application parses the
  mixed-turn summary before resolving a pending manual call
- **Preconditions**: two or more calls to the same configured auto tool and at
  least one non-auto call in one model turn
- **Frequency**: result loss is 5/5 once those conditions occur; the production
  frequency of such model turns was not measured

### Observable consequence

`user asks for a batch operation -> model emits two charge calls and one review
call -> Bifrost executes both charges -> Bifrost omits one charge result -> the
next-turn consumer sees one charge as unfinished -> charge runs again`

Measured impact: one duplicate side-effecting MCP execution per violation run,
5/5, versus zero in the distinct-name control, 0/5. The consumer is a
deterministic disclosed policy, not a live model. This proves the protocol
consequence at the closest consumer boundary without attributing stochastic
behavior to a provider.

Inferred impact: real agents may repeat writes, charges, notifications, or
other non-idempotent work, or may ask a user to resolve a call that already
completed. The exact production effect depends on the tool's idempotency and
the application's continuation policy.

### Bug or not

- **Expected behavior is the intended spec**: Bifrost's
  [agent-mode documentation](https://github.com/maximhq/bifrost/blob/1a17949cd80234de0ceb3018e941854f38b3f7a8/docs/mcp/agent-mode.mdx#L279-L318)
  says auto tools execute first, the returned content contains the executed
  tool results, and the application parses it to see what already executed.
  The same page explicitly documents parallel execution. The architecture
  example and `TestAgent_MixedAutoAndNonAutoTools` expect the summary. Those
  examples and tests cover one call per name and do not define same-name
  deduplication. The UI exposes the documented auto-execute list and contains
  no alternate response contract.
- **Maintainer ruling**: current docs, examples, tests, UI, original feature
  [PR #941](https://github.com/maximhq/bifrost/pull/941), later commits touching
  `agentadaptors.go`, and open, closed, and merged issue and PR searches contain
  no ruling that a tool name identifies an invocation or that repeated results
  may be discarded.
- **Supported usage**: explicit `tools_to_execute` and
  `tools_to_auto_execute` are the documented setup. Parallel calls use distinct
  IDs, and Bifrost documents parallel execution without requiring unique tool
  names.
- **Boundary crossed**: this is not a disclosure claim. The public gateway
  crosses its documented execution-to-application boundary by reporting an
  executed side effect as absent to the continuation consumer.
- **Maintainer fix**: preserve one client-visible result record per tool-call
  ID rather than collapsing records by tool name.
- **Label**: `bug`

## Gate 3: upstream status

Checked 2026-09-21. Bifrost's default branch is `dev`; the current head is the
exact tested commit
[`1a17949c`](https://github.com/maximhq/bifrost/commit/1a17949cd80234de0ceb3018e941854f38b3f7a8).
The latest HTTP transport release is
[`v2.2.1`](https://github.com/maximhq/bifrost/releases/tag/transports%2Fv2.2.1),
released 2026-09-18. The current default branch still contains the affected
code.

Searches covered open and closed issues, open, closed, and merged pull
requests, current source, path history, releases, changelogs, documentation,
examples, tests, and UI. Exact search terms:

```text
toolResultsMap
"Output from allowed tools calls"
"same tool" "agent mode"
"duplicate tool calls" agent
tool_call_id "agent mode"
mixed "auto execute" MCP
"executed tool results"
same-name tool result
agentadaptors
```

Search links:

- [issues: toolResultsMap](https://github.com/maximhq/bifrost/issues?q=is%3Aissue+toolResultsMap)
- [issues: same tool agent mode](https://github.com/maximhq/bifrost/issues?q=is%3Aissue+%22same+tool%22+%22agent+mode%22)
- [issues: executed tool results](https://github.com/maximhq/bifrost/issues?q=is%3Aissue+%22executed+tool+results%22)
- [pull requests: toolResultsMap](https://github.com/maximhq/bifrost/pulls?q=is%3Apr+toolResultsMap)
- [pull requests: same tool agent mode](https://github.com/maximhq/bifrost/pulls?q=is%3Apr+%22same+tool%22+%22agent+mode%22)
- [current code search](https://github.com/maximhq/bifrost/search?q=toolResultsMap&type=code)

`toolResultsMap` finds unrelated serialization PR #2345 plus the original
feature rollup, not this collision. `executed tool results` finds the original
agent-mode PRs and release rollups, also without this case. No matching ticket,
fix, changelog entry, documentation note, or deliberate behavior was found.

Classification: `novel`.

## Frozen invariant

- Issue writeup: this file
- Checker: `executed_tool_results_preserved` in
  `crates/harness/src/checks.rs`
- Conformance coverage: three issue-082 tests in
  `crates/harness/tests/conformance.rs`
- Invariant: every distinct auto-executed tool call retains exactly one
  client-visible result, independent of how a gateway formats the summary
- Vacuity guards: empty execution indexes, repeated tool-call IDs, malformed
  captures, missing markers, and a two-result index applied to the one-result
  control all score violations

## Validation

| Check | Command | Result |
|---|---|---|
| Public reproduction | `BIFROST_SOURCE=... BIFROST_BIN=... python3 transcripts/082/reproduce.py --runs 5` | PASS, violation and both controls 5/5 |
| Consumer boundary | same runner | PASS, duplicate 5/5 vs 0/5 control |
| Focused harness | `cargo test -p kairo --test conformance bifrost_agent_mode` | PASS, 3/3 |
| Workspace | `cargo test --workspace` | PASS, 181/181 |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS, 57 folders and 181 tests |

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| Both same-name calls execute | High | raw MCP request and response bytes, 5/5 |
| One result is lost | High | raw client responses and source probe, 5/5 |
| Loss is caused by tool-name keying | High | smallest-trigger controls plus exact source path |
| Duplicate consumer execution follows | High | identical deterministic continuation policy, 5/5 vs 0/5 |
| Live-model repeat behavior | Not claimed | consequence demonstrated without model nondeterminism |

## Test

`crates/harness/tests/conformance.rs` freezes the violation, both controls, the
five-run matrix, and the consumer-boundary consequence. The checker consumes
recorded execution IDs and result markers plus the complete raw HTTP response.
It does not depend on Bifrost field names, tool names, or the current map
implementation.
