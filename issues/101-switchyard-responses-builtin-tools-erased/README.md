# 101, Switchyard deletes Responses tools it cannot express and answers 200

- **Upstream**: [NVIDIA-NeMo/Switchyard](https://github.com/NVIDIA-NeMo/Switchyard). No issue and no merged pull request covers the Responses side; the mirror Anthropic case has open PR #917, and two closed drafts (#291, #958) state the maintainers' own remedy. Details in Gate 3.
- **Tool under test**: `switchyard-server` 0.3.0 built from commit [`ab7d26443899239632e97d9d9d8aedaa58c96b99`](https://github.com/NVIDIA-NeMo/Switchyard/commit/ab7d26443899239632e97d9d9d8aedaa58c96b99), `main` on 2026-10-14
- **Reproduced**: 2026-10-15, macOS arm64, Rust 1.99; deterministic local capture upstream plus live `gpt-4o-mini` on both routes

## What breaks

A Responses client that declares a hosted tool (`mcp`, `web_search_preview`,
`file_search`, `code_interpreter`, `computer_use_preview`, `image_generation`,
`local_shell`) gets HTTP 200 and a well-formed completion from any Switchyard
route whose target is not the Responses dialect, while the forwarded request
contains no trace of the tool. The model never learns it has a web search, a file
index, or an MCP server, and nothing records the removal: no error status, no
log line at debug level, no response header, no translation diagnostic.

The caller keeps its belief that the model can search. Every later answer is
produced without that capability. The same request, differing only in the
`model` field that selects the route, keeps every tool field on the same-dialect
route.

Scope of the claim: this is not about making hosted tools work on a Chat or
Anthropic target, which no dialect can do. It is about the gateway reporting
success while silently removing what the caller declared. The maintainers' own
closed drafts propose rejection for exactly this case (Gate 3).

## Gate 1: correctness

### Exact reproduction

```text
git clone https://github.com/NVIDIA-NeMo/Switchyard.git
cd Switchyard
git checkout ab7d26443899239632e97d9d9d8aedaa58c96b99
cargo build --release -p switchyard-server
cd /path/to/kairo
transcripts/101-switchyard-responses-builtin-tools-erased/run_repro.sh \
  /path/to/Switchyard/target/release/switchyard-server
```

- Expected behavior: a tool the target dialect cannot express is either carried
  in some faithful form or the request is refused. The caller is never told a
  request succeeded while its tool set was reduced.
- Observed behavior: HTTP 200, a normal `response.completed` object, and a
  forwarded body with no `tools` array at all.
- Raw request, response, and forwarded evidence:
  [`capture-*.jsonl`](../../transcripts/101-switchyard-responses-builtin-tools-erased/),
  one line per run holding the exact client request bytes, the exact forwarded
  request bytes and headers, the client response bytes, and the client status.
- Reproduction rate: 11 cells recorded as expecting erasure, 0/5 tool kept with
  5/5 HTTP 2xx in each (8 buffered hosted-tool cells, 1 streamed cell, and two
  data cells: `data-mcp-with-id-chat` and `data-preservation-bypass-chat`), and 7 control cells at 5/5 kept. Byte-identical on a
  second run.
- Smallest isolated trigger: change only the `tools[].type` of the same client
  request. `type: "function"` survives the identical route 5/5; `type: "mcp"`
  does not survive 0/5.

### Evidence, one run of each cell

The client request below is sent twice. Only the route differs.

```text
# client -> Switchyard, route model-chat (bug) and route model-resp (control)
POST /v1/responses
{"model":"model-chat","input":"CANARY_PROMPT","tools":[{"type":"mcp","server_label":"srv-canary","server_url":"https://example.invalid/mcp","require_approval":"never"}]}
-> HTTP 200 {"id":"chatcmpl-cap","object":"response",...,"status":"completed",...}

# Switchyard -> Chat target (bug cell), exact forwarded bytes
POST /chat/chat/completions
{"model":"captured-model","messages":[{"role":"user","content":"CANARY_PROMPT"}]}

# Switchyard -> Responses target (control cell), same client request, exact forwarded bytes
POST /resp/responses
{"model":"captured-model","input":"CANARY_PROMPT","tools":[{"type":"mcp","server_label":"srv-canary","server_url":"https://example.invalid/mcp","require_approval":"never"}]}
```

### Control ladder

| Rung | Result | Evidence |
|---|---|---|
| Direct provider | client search tool is honored by OpenAI, 2/2 | `live-consumer-boundary.jsonl` |
| Plain or bypass path | same-dialect route keeps the tool, 5/5, identical except the `model` field that selects the route | `capture-control-same-dialect-*.jsonl` |
| Routed path | cross-format route erases the tool, 0/5, 5/5 HTTP 2xx | `capture-bug-*.jsonl` |
| Trigger removed | `function` and `custom` tools survive the same cross-format routes, 5/5 | `capture-control-function-*.jsonl` |

### Affected types, all measured

| Declared `tools[].type` | Chat target | Responses target |
|---|---|---|
| `mcp` | erased, 5/5 (5/5 also on an Anthropic target) | preserved, 5/5 |
| `web_search_preview` | erased, 5/5 | preserved, 5/5 |
| `file_search` | erased, 5/5 | not run |
| `code_interpreter` | erased, 5/5 | not run |
| `computer_use_preview` | erased, 5/5 | preserved, 5/5 |
| `image_generation` | erased, 5/5 | preserved, 5/5 |
| `local_shell` | erased, 5/5 | not run |
| `function`, `custom` | preserved, 5/5 (5/5 on an Anthropic target) | preserved |

Streaming takes the same decode path: `capture-bug-mcp-chat-stream.jsonl`, 0/5
kept with 5/5 HTTP 2xx.

The erasure is a decode decision, not a filter on tool type. Give the same `mcp`
tool an `id` and it survives as an empty function named after that id, with the
`server_url` gone, so the model can call a tool nobody runs
(`capture-data-mcp-with-id-chat.jsonl`, asserted in the conformance suite).

### False-positive checks

- [x] `git ls-remote` confirms `ab7d2644` is still `main` HEAD on 2026-10-15.
- [x] The pinned tree differs from the commit in exactly one line,
      `rust-version = "1.96.0"` in the workspace `Cargo.toml`, made so this
      machine's toolchain can compile it. No source file differs.
- [x] Exercised the real public HTTP entry point of the real release binary.
- [x] Documented `passthrough` route, documented configuration, no unsupported
      provider layout, no disabled security control.
- [x] The upstream is a deterministic capture server, so model nondeterminism,
      provider tool policy, and transient faults cannot explain a difference
      between two routes that received the same client bytes.
- [x] The live comparison holds model, account, client request, and client SDK
      fixed and changes only the route's target dialect.
- [x] No credential is printed, copied, or committed. The rig uses a canary
      string; the live relay records `Authorization` as `<credential withheld>`.
- [x] Re-running the rig does not modify the committed captures: output goes to
      a scratch directory unless a directory is passed on purpose.

## Gate 2: usefulness

### Who gets bitten

- Affected user: any team running a Responses client, Codex CLI or an OpenAI
  Agents SDK application, through Switchyard on a route whose target is not the
  Responses dialect. That is the product's headline job: point a coding agent
  at Switchyard and let it pick the cheaper model.
- Real workflow: the client is configured with hosted tools, remote MCP servers
  or a built-in such as web search, and sends `/v1/responses`.

### The chain from user action to failure

1. The user asks a question that needs live data.
2. The client sends `/v1/responses` with `tools: [{"type": "web_search_preview"}]`.
3. Switchyard's Responses decoder deletes the tool and routes to the Chat
   target, whose request body has no `tools` field.
4. The gateway returns HTTP 200 and a `response.completed` object with no
   `web_search_call` item.
5. The model answers from its training snapshot and the UI shows a normal answer.

### Consumer boundary, measured with a real provider

Same client request, same model `gpt-4o-mini`, same real OpenAI account. Two
routes, both behind a local relay that records the forwarded bytes and then
forwards to `api.openai.com` unchanged. Question: who is the current US
president, with a source URL, using web search. Bytes in
[`live-consumer-boundary.jsonl`](../../transcripts/101-switchyard-responses-builtin-tools-erased/live-consumer-boundary.jsonl)
and [`live-forwarded.jsonl`](../../transcripts/101-switchyard-responses-builtin-tools-erased/live-forwarded.jsonl).

| Path | Runs | Forwarded `tools` | `web_search_call` | Answer |
|---|---|---|---|---|
| OpenAI directly, no gateway | 2 | n/a, SDK sends it | 2/2 | current officeholder, cited |
| Switchyard, Responses-dialect route | 2 | `[{"type":"web_search_preview"}]` | 2/2 | current officeholder, cited |
| Switchyard, Chat-dialect route | 2 | absent | 0/2 | "as of my last knowledge update in October 2023, the President ... was Joe Biden", uncited |

- Measured impact: the capability is missing from the model's input in 11/11
  capture erasure cells (in `data-mcp-with-id-chat` it survives only as an empty
  function with no server URL) and 2/2 live bug cells, and the failure is silent in every
  one, at both the client and the provider boundary.
- Inferred impact: any workflow that depends on a hosted tool degrades to
  ungrounded answers, and application code cannot detect the loss because the
  response is a normal success.

### Alternative explanations ruled out

- The client is not at fault. It declared a tool to a gateway whose purpose is
  choosing the target model; it cannot know which dialect will serve the call.
- The loss is not a deliberate restriction at the ingress. Two independent
  controls say so. First, the trigger cell: on the same route, changing only
  `tools[].type` from `mcp` to `function` takes the tool from 0/5 to 5/5, so the
  route and the client request shape are not what changed the outcome. Second,
  the same-dialect route keeps every tool field, 5/5.
- The loss is not a provider policy: the live request recorded at
  `api.openai.com/v1/chat/completions` had no `tools` field at all, so OpenAI
  never saw a search tool and never refused one.
- Adjacent to finding 100 (kairo PR #66, Anthropic server tools rebuilt by
  `plan_execute`) but a different defect: 100 turns a server tool into a
  request the upstream rejects, this one deletes the tool and reports success.

### Conditions and frequency

Preconditions: a Responses ingress, a target dialect other than
`openai_responses`, and at least one hosted tool in the request. Frequency inside
that condition: every such request, 5/5 in each of the nine hosted-tool cells.
Requests with only `function` or `custom` tools are unaffected.

### Bug or not

- **Is the expected behavior really the spec?** Yes, by the maintainers' own
  words. Closed draft PR #291: "Valid provider tools were dropped when they had
  no normalized function shape", and its scope is to "preserve OpenAI Responses
  built-in tools through decode and same-format encode". Closed draft PR #958:
  "Reject Anthropic provider tool requests when targeting Chat Completions or
  Responses, including when lossy conversions are allowed", and "Responses
  supports MCP, but this conversion is not yet implemented". Shipped
  `CHANGELOG.md` `[0.3.0]` section (lines 240-241): "Unsupported cross-format native output fails
  explicitly instead of disappearing." Merged PR #782 rejected rather than
  dropped a tool-choice restriction and called the alternative a security bug.
  Commit `ab7d2644` added `LossyConversion("MCP and provider tool encoding is
  not implemented")` to all three encoders. Every one of those says the caller
  must be told.
- **Have maintainers already ruled on it?** Partly, and not the other way.
  Open PR #917 proposes excluding Anthropic server tools from the normalized
  list, keeping same-format replay, for the mirror Anthropic case. That would
  still be a silent deletion from the caller's point of view, and it does not
  touch the Responses ingress measured here. No ruling accepts a silent deletion
  on the Responses side; the maintainers' own drafts say reject.
- **Is the trigger supported usage?** Yes. Documented route type, documented
  configuration, documented client dialect, nothing disabled or misconfigured.
- **Is a real boundary crossed?** Not a disclosure boundary. It is the
  correctness boundary between what the caller declared and what the model was
  given, with a success status reported across that gap.
- **What fix would a maintainer ship?** One sentence, matching #958: make the
  Responses decoder raise the `LossyConversion` its own encoders already raise
  for a tool with no function shape, instead of returning a discarded `false`.
- **Label**: `bug`.

## Gate 3: upstream status

- Date checked: 2026-10-15
- Upstream commit checked: `ab7d26443899239632e97d9d9d8aedaa58c96b99`, confirmed
  still `main` HEAD by `git ls-remote`
- Search terms: `mcp tool dropped`, `web_search_preview`, `built-in tool`,
  `provider tools`, `host tool`, `silently drop tool`, `MCP and provider tool
  encoding is not implemented`, `LossyConversion`, `Responses tools cross-format`,
  `preserve provider tools`, `omit_input_items`, `Codex tools never reach a
  translated upstream`, plus repository-wide issue and pull request search
- Directly read and characterized: #917, #958, #782, #729, #291, #619, #993,
  #955, #857, #152
- Releases, changelog, docs: v0.3.0 `CHANGELOG.md`, `docs/known_issues.md`,
  `docs/reference/toml_schema.md`, `crates/protocol/README.md`

What exists upstream:

| Artifact | State | Relation to this claim |
|---|---|---|
| #291 preserve provider tools and enforce field policy | closed draft, 2026-08-04 | names this exact defect for Responses built-in tools; superseded by #993, which added the IR type but not the decode path |
| #958 reject Anthropic provider tools on Chat and Responses targets | closed draft, 2026-10-08 | states the intended remedy, reject; explicitly says the Responses MCP conversion is unimplemented |
| #917 keep Anthropic server tools out of translated requests | **open**, 2026-10-06 | mirror Anthropic case; proposes silent exclusion, keeps same-format replay; does not touch the Responses ingress |
| #515 Codex tools never reach a translated upstream | closed | names the same fallthrough to `push_responses_id_tool` and the same discarded `bool`, scoped to Codex `custom` and `additional_tools`; fixed at HEAD by #648, so it does not cover hosted tools |
| #516 fix for #515 | closed unmerged | same scope, same function; #515 was closed by #648 instead, merged 2026-09-10 |
| #782 translate `allowed_tools`, reject when not possible | merged | same "reject rather than drop" precedent, called a security bug there |
| #729 preserve Responses built-in tool history | merged | rejects Chat and Anthropic translations that would turn the item into text |
| #993 represent MCP tools and backend history | merged, HEAD | added `Tool::Mcp` plus encoder errors; `Tool::Mcp` is unreachable from the wire because no decoder constructs it |

Classification:

- [x] Novel for the exact claim: no issue and no merged or open pull request
      covers the Responses ingress deleting declared hosted tools. The behavior
      is discussed upstream in closed drafts #291 and #958, which name it as a
      defect and prescribe rejection.
- [ ] Duplicate, open and still reproducible. Open PR #917 is the mirror
      Anthropic case and proposes a different remedy.
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket, for the mirror case only
- [ ] Incomplete, current upstream state could not be verified

## Root cause

`crates/switchyard-translation/src/codecs/responses/buffered.rs:1016-1109`,
`decode_responses_tools`. The function takes no diagnostics argument and no
translation policy, so it cannot record anything. Its branches are: a namespace
container, `type: "custom"` (1056), `type: "function"` (1079), a tool with no
`type` but a `name` (1102), and everything else (1104). A hosted tool such as
`mcp` has a `type` that is neither `custom` nor `function`, so control reaches
1105:

```rust
1104:        } else {
1105:            push_responses_id_tool(&mut out, tool);
1106:        }
```

`push_responses_id_tool` (1136) returns `false` unless the tool object carries
an `id`, the `bool` is discarded at the call site, and the tool leaves
`request.tools` with no record. `push_responses_function_tool` is reached only
from the `type: "function"` arm (1098) or from the no-`type` arm (1103), which is
why adding a `name` to the same `mcp` tool changes nothing and why adding an
`id` turns it into an empty function.

The second half is that the loss cannot be reported even if the decode wanted
to. `crates/switchyard-translation/src/helpers.rs:31-43` returns only the decoded
request and drops `TranslationOutput.diagnostics`, so a `push_lossy` warning
would never reach the caller or a log. The `LossyConversion` errors added to all
three encoders by `ab7d2644` are therefore unreachable from this input, and
`Tool::Mcp`, the type meant to carry these tools, is constructed only by its
`Deserialize` impl and tests, never by a decoder.

The same-dialect control cell does not exercise this decoder at all, and the
writeup should not imply that it does. `ResponsesCodec::encode_request`
(`buffered.rs:185-192`) returns the preserved original body through
`exact_preserved_request` (`crates/switchyard-translation/src/util.rs:299-312`,
fed by `capture_request_preservation` at 277-288) whenever the target format
matches the ingress format, so that route replays the client's bytes instead of
rebuilding them from the IR. The committed data cells prove it: a top-level
field no IR rebuild could emit survives on the Responses route and is gone on the
Chat route.

```text
# capture-data-preservation-bypass-resp.jsonl, exact forwarded bytes
{"model":"captured-model","input":"CANARY_PROMPT","tools":[{"type":"mcp",...}],"zz_unknown_field":"keepme"}

# capture-data-preservation-bypass-chat.jsonl, same client fields, exact forwarded bytes
{"model":"captured-model","messages":[{"role":"user","content":"CANARY_PROMPT"}]}
```

The control that isolates the decoder is therefore the trigger cell, where the
route, the client, and the request shape are fixed and only `tools[].type`
changes.

## Wire evidence

- `expected.http`: the declared tools reach the target, or the client receives a
  non-2xx status. See the control cells.
- `observed.http`: `capture-bug-*.jsonl`, forwarded body without `tools`, client
  status 200. Live: `live-forwarded.jsonl`, the Chat route with no `tools` key.
- `evidence`, not redacted: the bytes under dispute are the `tools` arrays.

## Frozen invariant

- Checker: `host_tool_not_silently_erased` in `crates/harness/src/checks.rs`
- Conformance tests: the five `switchyard_101_*` cases in
  `crates/harness/tests/conformance.rs`
- Why invariant level: a gateway that answers 2xx must have carried every
  declared tool to the target in some representation, or refused. The checker
  reads only the client's declarations and the forwarded body, names no
  Switchyard function, and applies to any gateway.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `transcripts/101-switchyard-responses-builtin-tools-erased/run_repro.sh SWITCHYARD_BIN` | PASS, 11 cells expecting erasure at 0/5 kept and 5/5 HTTP 2xx, 7 control cells 5/5 kept |
| Consumer boundary | `transcripts/101-switchyard-responses-builtin-tools-erased/live_consumer.py 2` | PASS, control cells 4/4 searched with forwarded tool present, bug cells 0/2 searched with no `tools` forwarded |
| Harness | `cargo test --workspace` | PASS, 272 tests (204 conformance, 68 unit) |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS |
| Independent review | `.github/agents/kairo-reproduction-reviewer.agent.md` | third pass on PR #67: all three gates PASS, NEEDS EVIDENCE only on README counts, fixed |

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized provider response
      is committed. The live relay records `Authorization` as
      `<credential withheld>`.
- [x] Only endpoints already configured in the local environment were used, with
      a test prompt and a public-knowledge question.
- [x] The pull request contains one finding.
- [x] Unrelated working tree changes are preserved.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS
- Overall: ACCEPT

## Independent review

First pass, 2026-10-15: REJECT. Correctness and usefulness passed; the upstream
record failed because the writeup asserted no maintainer artifact addressed the
silent deletion while open PR #917 and closed drafts #291 and #958 do, the
#619 characterization was a misrepresentation, the root-cause paragraph named the
wrong branch, two tool types were listed without being measured, and the rig
wrote scratch files into the tracked tree. All six items were corrected:
the table in Gate 3 replaced the blanket assertion, the root cause follows the
actual path, `computer_use_preview` and `image_generation` gained measured cells,
`run_repro.sh` writes to a scratch directory, and the stale references to an earlier
finding number in the rig and the checker were renamed. A second pass returned
NEEDS EVIDENCE with no new capture required: the same-dialect control bypasses
the decoder through exact body preservation, so the writeup called it a decoder
control it is not; the `model` field that selects a route means the bug and
control requests differ by that field and are not byte-identical; two line
numbers in the root cause were off by one; `issues/SCOREBOARD.md` still carried
the old cell counts, line ranges, and the superseded 1098 citation; three stale
finding-number strings remained in the rig and the checker; and the nearest upstream
artifacts #515 and #516 were missing from the record. All nine items are fixed
here: the preservation bypass is stated in the root cause with two committed
data cells as evidence, the isolation argument now rests on the trigger cell, the
line numbers are 1105 and 1016-1109, `helpers.rs:31-43`, the SCOREBOARD row
matches the rig, the stale strings are gone, and #515/#516 are recorded with the
reason they do not transfer.

Third pass, 2026-10-15, on PR #67 head `83ad397`: all three gates PASS, with the
reproduction rerun byte-identical and three new reviewer-owned probes (GA
`web_search` beside a function tool is partially erased, `tool_choice:
"required"` does not prevent erasure, `file_search` on the Anthropic route is
erased, each 3/3). Overall NEEDS EVIDENCE only because the README count check
was stale. Fixed together with five wording items: the #516 state, the
changelog section, the cell breakdown, the measured-impact count, and the
SCOREBOARD upstream cell. The reviewer noted it shares a model family with the
author.