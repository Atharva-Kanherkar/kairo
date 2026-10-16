# 103, Bifrost drops the namespace OpenAI gives a searched-in tool call, so Claude Code tool search halts on GPT-5.4 and GPT-5.5 (not on GPT-6)

- **Upstream**: [maximhq/bifrost](https://github.com/maximhq/bifrost). No issue or
  pull request reports this on 2026-10-16. It is the next failure on the path
  that [#8361](https://github.com/maximhq/bifrost/issues/8361) reported and
  [#8503](https://github.com/maximhq/bifrost/pull/8503) fixed on 2026-10-14
- **Tool under test**: Bifrost HTTP transport built from the default branch `dev`
  at
  [`6c0f795da21130ca24dcf23f267d5edcb0cbcea1`](https://github.com/maximhq/bifrost/commit/6c0f795da21130ca24dcf23f267d5edcb0cbcea1)
  (the branch head on 2026-10-16, core from the same commit). Context build: the
  latest release
  [`transports/v2.3.0`](https://github.com/maximhq/bifrost/releases/tag/transports%2Fv2.3.0)
  at `9237ec06672406e437b3cb33fc793ffd0403b3ae` with the published core `v1.12.0`
- **Reproduced**: 2026-10-16, 06:24Z to 06:29Z, macOS arm64. Public
  `POST /anthropic/v1/messages`, Anthropic dialect in, OpenAI Responses out,
  provider `openai` with its default base URL replaced by a recording relay to
  `https://api.openai.com`. Live models `openai/gpt-5.4-mini` (served as
  `gpt-5.4-mini-2026-03-17`) and `openai/gpt-5.5`. Claude Code `2.1.289` with
  `ENABLE_TOOL_SEARCH=true` and a stdio MCP server. Model scope rerun the same
  day on the newer `dev` head `9bcea6777488adc674dc8d326bd4933b71746a22` against
  `openai/gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-luna`, `gpt-6-astra`, with
  `gpt-5.4-mini` as the same-build control
- **Model scope label**: Claude Code's client tool search reproduces on GPT-5.4 and
  GPT-5.5 only; it does **not** reproduce on any of the four GPT-6 models. Anthropic's
  server-side tool search reproduces on every model tested, GPT-6 included

## Claim

On `/anthropic/v1/messages` to OpenAI, Bifrost turns a tool
search (Claude Code's client `ToolSearch`, or Anthropic's server-side
`tool_search_tool_bm25`) into OpenAI `tool_search` items. OpenAI loads the
deferred tool into a namespace named after the tool and returns the model's call
with `"namespace": "mcp__orders__lookup_order"`. An Anthropic `tool_use` has no
field for that, and when the client replays the call Bifrost rebuilds the
`function_call` without it. OpenAI rejects the request:

```text
400 Missing namespace for function_call 'mcp__orders__lookup_order'. It does not
exist in the default namespace. Round-trip the model's function_call item with
its namespace field included.
```

The tool has already run by then. Every later turn of the conversation replays
the same call, so the session cannot continue.

Whether OpenAI sets the namespace depends on the model and the search path, so the
failure does too. GPT-5.4 and GPT-5.5 set it on both paths. The four GPT-6 models
tested return the call to a tool that **client** search loaded with no namespace
and accept the replay without one, so Claude Code works there; they still set it
on the **server-side** search path, which fails the same way. See
[Model scope](#model-scope).

## What breaks

Claude Code behind Bifrost, on GPT-5.4 or GPT-5.5, with MCP servers and tool search
on. This is the setup Bifrost's unreleased changelog says now works: "deferred
tools on OpenAI gpt-5.4+ ... get the tool_search OpenAI requires instead of a
400, a client search function (Claude Code's ToolSearch) becomes a
client-executed tool_search with its history replayed". The first two turns do
work. The third does not:

1. Claude Code calls `ToolSearch`, gets a `tool_reference` back, and the model
   calls the MCP tool `mcp__orders__lookup_order`.
2. Claude Code runs the MCP tool. The recorded MCP ledger shows one execution in
   every run.
3. Claude Code sends the tool result. Bifrost forwards the history without the
   namespace, OpenAI returns 400, Claude Code retries twice more, and prints
   `API Error: 400 Missing namespace for function_call ...` and exits 1, 5/5.
4. `claude -p --continue "Please try again."` replays the same history and fails
   the same way, 5/5. The tool does not run again, and the session is stuck.

With `ENABLE_TOOL_SEARCH=false`, the same Claude Code, gateway, model and question
answers `Order A-1001 is SHIPPED-CANARY-7731`, 5/5.

An Anthropic SDK application that declares Anthropic's server-side
`tool_search_tool_bm25` with `defer_loading` tools hits the same 400 on its second
turn, 5/5, with turn 1 generated live by the model.

## Model scope

Same build (`dev` `9bcea677`), same rig, same requests, live OpenAI:

| Path | gpt-5.4-mini | gpt-6.1-sol | gpt-6-sol, gpt-6-luna, gpt-6-astra |
|---|---|---|---|
| Client search replay (Claude Code's `ToolSearch` history) | 400 `Missing namespace`, 4/4 | served, 6/6 buffered and streamed | served, 6/6 per model |
| Real Claude Code with MCP, tool search on | halts (5/5 on `6c0f795d`) | answers, 3/3; `--continue` served 3/3 | not run |
| Server-side search (`tool_search_tool_bm25`), turn 2 | 400, 2/2 | 400, 3/3 | 400, 2/2 per model |
| Namespace OpenAI set on the returned call, client search | the tool's name | none | not recorded (no turn 1 in the minimal replay) |
| Namespace OpenAI set on the returned call, server-side search | the tool's name | the tool's name | the tool's name |

GPT-6 also serves Bifrost's forwarded client-search body with `namespace` added
(`direct/namespace-restored-NN` on `gpt-6.1-sol`, 3/3), so restoring the namespace
does not break the models that do not need it. Captures:
[`capture-9bcea677/`](../../transcripts/103/capture-9bcea677/), one directory per
model, each with its own `results.json`.

## Wire evidence

Everything is under [`transcripts/103/`](../../transcripts/103/). Each case
directory holds the client hop (`client/`) and the Bifrost to OpenAI hop
(`upstream/`) as raw HTTP. The full matrix is
[`capture/results.json`](../../transcripts/103/capture/results.json).

What OpenAI returned on turn 1 of a hosted run
(`capture/dev-6c0f795d/minimal/hosted-nonstream-01/upstream/001-response.http`):

```json
{"type": "tool_search_call", "execution": "server", "arguments": {"paths": ["mcp__orders__lookup_order"]}, ...}
{"type": "tool_search_output", "execution": "server", "tools": [...], ...}
{"type": "function_call", "call_id": "call_ku90uHGzeDSBwJugh1hKMCay",
 "name": "mcp__orders__lookup_order", "namespace": "mcp__orders__lookup_order",
 "arguments": "{\"order_id\":\"A-1001\"}", ...}
```

What the client sent back, an ordinary Anthropic `tool_use` and `tool_result`.
What Bifrost then forwarded on turn 2 (`.../upstream/002-request.http`, input
excerpt):

```json
{"type": "tool_search_call", "execution": "server", "arguments": {"paths": ["mcp__orders__lookup_order"]}, ...},
{"type": "tool_search_output", "execution": "server",
 "tools": [{"type": "function", "name": "mcp__orders__lookup_order", "defer_loading": true, ...}]},
{"type": "function_call", "call_id": "call_ku90uHGzeDSBwJugh1hKMCay",
 "name": "mcp__orders__lookup_order", "arguments": "{\"order_id\": \"A-1001\"}", ...}
```

No `namespace`. OpenAI answered `400`, `"param": "input[3].namespace"`.

The direct control removes any doubt about which bytes matter: the body Bifrost
forwarded for `minimal/deferred-nonstream-01`, sent straight to OpenAI, gets the
same 400, 5/5. The same body with only `"namespace": "mcp__orders__lookup_order"`
added to the replayed `function_call` gets 200 and the answer, 5/5. The
conformance test checks that the two bodies differ by that field and nothing
else.

## Root cause

`core/providers/openai/toolsearch.go` at `6c0f795d`, added by #8503.
`provisionToolSearch` (line 30) rewrites the search history for OpenAI:

- `mapClientToolSearchFunction` (line 69) turns calls to the client's search
  function into `tool_search_call` items and their `tool_reference` results into
  `tool_search_output` items that load the referenced tools (lines 92 to 127).
- `replayTypedToolSearchCalls` (line 136) does the same for Anthropic's
  server-side search replayed as `server_tool_use` plus `tool_search_tool_result`.

Neither touches the later `function_call` items for the tools those outputs
loaded, and the Anthropic ingress has nowhere to carry a namespace, so those
calls go out with no `namespace`. OpenAI requires it for a tool loaded by tool
search. The unit tests added with the feature
(`TestToOpenAIResponsesRequest_MapsClientToolSearchFunction`,
`..._ReplaysHostedToolSearchHistory`, `..._AnthropicIngressClientToolSearch` in
`core/providers/openai/responsesmarshal_test.go`) stop at the search itself and
never replay a call to the loaded tool.

The Responses route of the same build is not affected when the client can carry
the field: `/v1/responses` with the namespace on the `function_call` is served,
5/5.

## Gate 1: correctness

### Exact reproduction

```text
# Bifrost dev, from a full checkout of maximhq/bifrost
git checkout 6c0f795da21130ca24dcf23f267d5edcb0cbcea1
make setup-workspace                         # GOTOOLCHAIN=go1.27.2+auto
mkdir -p transports/bifrost-http/ui && echo '<html></html>' > transports/bifrost-http/ui/index.html
(cd transports && go build -o /tmp/bifrost-dev ./bifrost-http)

# Release v2.3.0, against the published modules
git checkout transports/v2.3.0               # 9237ec06672406e437b3cb33fc793ffd0403b3ae
(cd transports && GOWORK=off go build -o /tmp/bifrost-v230 ./bifrost-http)

# Matrix (needs OPENAI_API_KEY in the environment or in --env-file)
python3 transcripts/103/rig/reproduce.py \
  --build dev-6c0f795d=/tmp/bifrost-dev --build v2.3.0=/tmp/bifrost-v230 \
  --output /tmp/kairo-103 --runs 5 --extra-model openai/gpt-5.5 \
  --claude dev-6c0f795d --env-file .env
```

[`rig/build-and-run.md`](../../transcripts/103/rig/build-and-run.md) has the full
notes. The rig starts each build with
[`rig/config.json`](../../transcripts/103/rig/config.json), which configures one
`openai` key from `OPENAI_API_KEY` and points the provider at the upstream
relay. Nothing else is configured.

- Expected behavior: a call the model made to a tool loaded by tool search is
  replayed with the namespace OpenAI gave it, and the turn is served.
- Observed behavior: the replayed `function_call` has no `namespace`, and OpenAI
  returns 400 `Missing namespace for function_call 'mcp__orders__lookup_order'`.
- Raw request evidence: `capture/dev-6c0f795d/minimal/deferred-*/client/001-request.http`.
- Raw response evidence: the matching `client/001-response.http`.
- Forwarded-request evidence: `upstream/001-request.http` and
  `upstream/001-response.http` in each case.
- Reproduction rate: 10/10 for the minimal Anthropic request (5 buffered, 5
  streamed), 5/5 for the live hosted search, 2/2 on `gpt-5.5`, and 5/5 in Claude
  Code plus 5/5 on `--continue`.
- Smallest isolated trigger: one Anthropic Messages request whose history holds
  a `ToolSearch` call, its `tool_reference` result, a call to the loaded tool, and
  that call's result, with the tool marked `defer_loading` (`minimal.deferred_body`
  in the rig). Sending the same tool eagerly, with no search, is served.

### Hosted closed-source evidence

N/A. Bifrost is open source and was built and run locally. OpenAI is the live
provider, not the target.

### Control

```text
# all in the same matrix run, same build, same model, same question
direct/namespace-restored-NN   OpenAI, no gateway, Bifrost's body + namespace
responses/with-namespace-NN    Bifrost /v1/responses, the client sends namespace
minimal/eager-*-NN             Bifrost /anthropic, tool eager, no search
claude/tool-search-off-NN      Claude Code, ENABLE_TOOL_SEARCH=false
v2.3.0/minimal/*               latest release, same requests
```

| Rung | Result | Evidence |
|---|---|---|
| Direct provider, Bifrost's forwarded body as recorded | 400 `Missing namespace`, 5/5 | `direct/as-forwarded-NN` |
| Direct provider, same body plus `namespace` only | 200 with the canary, 5/5 | `direct/namespace-restored-NN` |
| Same build, Responses route, client sends `namespace` | 200 with the canary, 5/5 | `responses/with-namespace-NN` |
| Same build, Responses route, client omits `namespace` | 400 `Missing namespace`, 5/5 | `responses/without-namespace-NN` |
| Anthropic route, deferred tool and search (trigger) | 400 `Missing namespace`, 10/10 | `minimal/deferred-*-NN` |
| Anthropic route, same history, tool eager (trigger removed) | 200 with the canary, 10/10 | `minimal/eager-*-NN` |
| Anthropic route, Anthropic server-side search, live turn 1 | turn 1 200 with `namespace`, turn 2 400, 5/5 | `minimal/hosted-nonstream-NN` |
| Claude Code, tool search on | MCP tool runs once, then exit 1 with the 400, 5/5 | `claude/tool-search-on-NN` |
| Claude Code, tool search off | answer with the canary, exit 0, 5/5 | `claude/tool-search-off-NN` |

- Control result: OpenAI serves the identical history once the namespace is
  present, and the same Bifrost build forwards the namespace when the client
  dialect can carry it. The only failing hop is Anthropic to Responses, and only
  when a tool search loaded the called tool.
- Why this attributes the failure to the claimed layer: the provider accepts the
  corrected bytes, the client sent a valid Anthropic history (the eager control
  and Claude Code's own first turns are served), and the forwarded body is the
  only place the field goes missing.

On `v2.3.0` the same deferred request fails one turn earlier with
`Deferred tools require tools.tool_search` (#8361), 10/10, and the hosted request
with `Unknown parameter: 'tools[0].name'`, 5/5. Both are fixed on `dev`. The
namespace defect is therefore new on the current default branch: it is the next
failure once #8503 lets the conversation reach a call to the loaded tool. The
eager control is served on `v2.3.0` too, 10/10.

### False-positive checks

- [x] Tested the exact cited behavior, not a similar symptom. The error is
      OpenAI's own message for this field, and adding the field alone clears it.
- [x] Pinned the target release or commit. Both binaries carry
      `vcs.revision` and `vcs.modified=false` in their Go build info; SHA-256 in
      `rig/build-and-run.md`.
- [x] Ruled out bad configuration and malformed input. The config is one OpenAI
      key and a base URL. The minimal request is the history Claude Code itself
      sends, and the eager form of it is served.
- [x] Ruled out model nondeterminism. The rejection is a request validation
      error and does not depend on model output. The hosted case uses live turn
      1 output and the two models tested behave the same.
- [x] Confirmed the failure is not created only by the mock or harness. There is
      no mock: OpenAI is live, the relay passes bytes through unchanged, and the
      Claude Code runs use the real CLI and a real stdio MCP server.
- [x] Sanitized all recorded evidence. Credentials, OpenAI organization and
      project ids, request and trace ids, rate-limit values and the Claude Code
      device id are replaced with marked `[REDACTED: ...]` values, in headers and
      where Bifrost echoes provider headers inside a JSON body.

## Gate 2: usefulness

### Who gets bitten

- Affected user or customer: teams that run Claude Code through Bifrost on
  OpenAI GPT-5.4 or GPT-5.5 (Bifrost's Claude Code guide uses `openai/gpt-5.5` as
  its example) and connect MCP servers. Claude Code on GPT-6 is not affected.
  Anthropic SDK applications that use the server-side tool search are affected on
  GPT-5.4, GPT-5.5 and GPT-6. The same gate covers Azure and Bedrock
  GPT deployments (`defaultSupportsToolSearch`), which were not tested.
- Real workflow: a coding or ops agent session in which the model has to find an
  MCP tool through tool search and then call it.
- Preconditions and likely frequency: Claude Code keeps MCP tools deferred
  behind a custom `ANTHROPIC_BASE_URL` only with `ENABLE_TOOL_SEARCH=true`
  ([Claude Code docs](https://code.claude.com/docs/en/mcp), "Configure tool
  search"), and Bifrost's dev branch added support for exactly this (#8361,
  #8503). Once those hold, every session fails on the turn after its first call
  to a deferred tool, 100% of the time on GPT-5.4 and GPT-5.5. Anthropic SDK
  applications that use the server-side tool search with deferred tools fail the
  same way on every model tested. The defect is on the default branch, not in a release yet; `v2.3.0` fails
  this workflow earlier for a reason `dev` fixed.

### Observable consequence

- User action: ask Claude Code a question that needs an MCP tool.
- Wire-level defect: the replayed `function_call` for the searched-in tool has no
  `namespace`.
- End-user or agent-level failure: the MCP tool runs, then the session stops with
  `API Error: 400 Missing namespace for function_call 'mcp__orders__lookup_order'`
  and exit status 1. The answer is never produced. Resuming the session fails
  the same way.
- Consumer-boundary demonstration or transcript:
  `capture/dev-6c0f795d/claude/tool-search-on-NN/claude-stdout.jsonl`,
  `.../continue/claude-stdout.jsonl` and `ledger.jsonl`.
- Measured impact: on `gpt-5.4-mini`, 5/5 Claude Code sessions halted after one
  MCP execution; 5/5 resumed sessions failed with no further execution; 5/5
  sessions answered with tool search off. On `gpt-6.1-sol`, 3/3 Claude Code
  sessions with tool search on answered. Live server-side-search conversations
  failed on turn 2: 5/5 on GPT-5.4 and 9/9 across the four GPT-6 models.
- Inferred impact, clearly labeled: a side-effecting MCP tool (a refund, a
  deploy, a ticket update) has already acted when the session dies, so a user
  who starts over to get past the error repeats the action. Not measured here;
  the rig used a read-only lookup.

### Bug or not

- Expected behavior confirmed as intended spec, not a stale doc line: Bifrost's
  own unreleased `core/changelog.md` entry for #8503 says Claude Code's
  `ToolSearch` on gpt-5.4+ works with "its history replayed as
  tool_search_call / tool_search_output", and #8361 names Claude Code with
  `ENABLE_TOOL_SEARCH=true` as the case to fix. OpenAI's error names the required
  field. Other clients treat the same 400 as a bug and fixed it by carrying or
  restoring the namespace:
  [dust-tt/dust#33775](https://github.com/dust-tt/dust/pull/33775) ("Namespace
  deferred tool calls from other providers when replaying to OpenAI", the tool's
  own name as namespace),
  [earendil-works/pi#7709](https://github.com/earendil-works/pi/issues/7709),
  [github/copilot-cli#4519](https://github.com/github/copilot-cli/issues/4519),
  [vercel/ai#20265](https://github.com/vercel/ai/issues/20265).
- Maintainer ruling searched (commit, PR, comment, denylist) and result: none.
  #8503 adds the search replay (and #8555 the Anthropic side of it) and says
  nothing about the calls that follow it. No comment or test chooses to drop the namespace.
- Trigger is supported usage with default or recommended settings: yes. Default
  gateway config with one OpenAI key; a model in Bifrost's tool-search gate; the
  documented Claude Code setting for tool search behind a gateway; a stdio MCP
  server.
- Boundary crossed: not a disclosure claim.
- Fix a maintainer would ship, in one sentence: when `provisionToolSearch`
  replays a `tool_search_output` that loads deferred functions, set `namespace`
  to the function's name on later `function_call` items for those functions
  that have none.
- Label: `bug`, scoped by model: Claude Code's path on GPT-5.4 and GPT-5.5;
  the server-side search path on every model tested.

## Gate 3: upstream status

- Date checked: 2026-10-16
- Upstream release, commit, or hosted deployment checked: `dev` at `6c0f795d`
  (no newer commit on `dev` at the time of the check), release `transports/v2.3.0`
- Search terms used: `Missing namespace`, `Missing namespace for function_call`,
  `namespace function_call`, `tool_search namespace`, `namespace`,
  `ENABLE_TOOL_SEARCH`, `tool search`, `deferred tools`, `defer_loading`,
  `tool_search_output`, `ToolSearch`
- Issues searched: maximhq/bifrost, open and closed. Related, different
  defects: [#8361](https://github.com/maximhq/bifrost/issues/8361) (closed by
  #8503, turn 1 400 `Deferred tools require tools.tool_search`),
  [#8348](https://github.com/maximhq/bifrost/issues/8348) and
  [#8291](https://github.com/maximhq/bifrost/issues/8291) (open, `tool_reference`
  and `tool_search_tool_result` dropped on other paths),
  [#5428](https://github.com/maximhq/bifrost/issues/5428) (xAI namespace tools)
- Pull requests searched: maximhq/bifrost, open, merged and closed.
  [#8503](https://github.com/maximhq/bifrost/pull/8503),
  [#8502](https://github.com/maximhq/bifrost/pull/8502),
  [#8555](https://github.com/maximhq/bifrost/pull/8555) added the search replay;
  [#8349](https://github.com/maximhq/bifrost/pull/8349) keeps `tool_reference`
  blocks; [#5065](https://github.com/maximhq/bifrost/pull/5065) restores a
  namespace on responses after the compat plugin flattens namespace tools, a
  different direction and path. None touches replayed calls to searched-in tools.
- Releases, changelog, and documentation searched: `core/changelog.md`
  (unreleased section), the `v2.3.0` release notes, `docs/` for tool search and
  Claude Code
- Relevant commits searched: `git log` on `core/providers/openai` and
  `core/providers/anthropic` since `transports/v2.3.0`
- Matching links: none for this defect

Classification:

- [x] Novel
- [ ] Duplicate, open and still reproducible
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

Explain what is new or useful here if the report is already known upstream: not
known. It is new on `dev` and not in a release yet, so it can be fixed before the
#8503 work ships.

## Frozen invariant

- Issue writeup: this file.
- Checker added or updated: `replayed_calls_keep_returned_namespace` in
  `crates/harness/src/checks.rs`. Given the provider's earlier responses and the
  forwarded request, a replayed `function_call` must carry the `namespace` the
  provider returned on the same `call_id`. A call returned without one is not
  judged, because OpenAI accepted its replay both ways.
- Conformance test added or updated: twelve `bifrost_103_*` tests in
  `crates/harness/tests/conformance.rs` over the committed captures, and one unit
  test in `checks.rs`.
- Why the checker tests the invariant rather than one implementation detail: it
  reads only provider responses and the forwarded body and applies the rule in
  OpenAI's own error ("round-trip the model's function_call item with its
  namespace field included"), so it judges any gateway and makes no assumption
  about which models set a namespace. It flags every real conversation OpenAI
  rejected (Claude Code on GPT-5.4, server-side search on GPT-5.4 and all four
  GPT-6 models) and passes the Claude Code conversations OpenAI served on
  GPT-6, where no namespace came back. An earlier version assumed the namespace
  always equals the tool name and would have flagged those served GPT-6 bodies;
  it was replaced after the GPT-6 runs. Disabling the comparison fails two of
  the twelve tests and the unit test; the synthetic-history cases are judged by
  OpenAI's status in the tests, not by the checker.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction, GPT-5 | `python3 transcripts/103/rig/reproduce.py ... --runs 5` on `6c0f795d` | 10/10 minimal, 5/5 hosted, 2/2 gpt-5.5, 5/5 Claude Code, 5/5 `--continue` |
| Control, GPT-5 | same run | direct restored 5/5, Responses route 5/5, eager 10/10, tool search off 5/5 |
| Model scope, GPT-6 | same rig on `9bcea677`, `--model openai/gpt-6.1-sol --runs 3` and `--runs 2` per other GPT-6 model | client-search replay served 24/24 (plus 3/3 sent directly and 3/3 on `/v1/responses` without `namespace`), Claude Code answered 3/3; server-side search 400 9/9; same-build `gpt-5.4-mini` 400 6/6 |
| Harness | `cargo test --workspace` | pass (227 conformance, 74 unit, 10 adversarial) |
| Formatting | `cargo fmt --all -- --check` | pass |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | pass |
| README counts | `python3 tools/update-readme-counts.py --check` | pass |

## Confidence

High that the defect is real and current on `dev`: the provider's own error names
the field, the corrected body is served, and the real client fails every run on
GPT-5.4. High that its reach depends on the model: Claude Code's path does not
fail on any of the four GPT-6 models, while the server-side search path fails on
all of them. Not verified: Azure and Bedrock GPT deployments, which pass the same
gate in code; `gpt-5.6` models; a side-effecting tool.

## Independent review

### Round 1: ACCEPT

On 2026-10-16 an independent reviewer (OpenAI Codex CLI 0.159.3, not a Claude
model) followed `.github/agents/kairo-reproduction-reviewer.agent.md` against
head `a236353`. It cloned both repositories fresh, rebuilt `6c0f795d` and the
newer `dev` head `9bcea677` with `vcs.modified=false`, and reran the committed rig
(3/3 per cell, 2/2 on `gpt-5.5`) plus its own requests with different tool names,
prompts and order ids: client search 3/3 buffered and 3/3 streamed, server-side
search 3/3, each forwarded body rejected directly and served with `namespace`
alone, eager controls served. Real Claude Code halted 3/3 after one MCP execution
and failed again on resume 3/3; with tool search off it answered 3/3. Upstream
search found no matching issue or pull request. Repository checks passed. No
blocking findings. Non-blocking: the `dev` head moved during the review (the
defect remains), and `reproduce.py` reports outcomes rather than failing on an
unexpected matrix.

### Changes after round 1

The author then ran the rig on GPT-6 and found Claude Code's path does not
reproduce there. The title, claim, scope and usefulness sections now state the
model scope; the GPT-6 captures were added under `transcripts/103/capture-9bcea677/`;
and the checker was replaced with `replayed_calls_keep_returned_namespace`,
because the round 1 checker assumed every searched-in call needs the tool's name
as namespace and would have flagged GPT-6 replays that OpenAI serves.
