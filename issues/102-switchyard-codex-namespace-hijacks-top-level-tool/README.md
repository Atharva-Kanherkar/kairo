# Finding

## Claim

Switchyard stamps an MCP `namespace` onto a tool call the model made against a
top-level tool, so Codex dispatches the call to a server the model never chose.
The ambiguity guard behind that namespace counts other namespaces, but not the
top-level tools that own the same name.

- Upstream project: [NVIDIA-NeMo/Switchyard](https://github.com/NVIDIA-NeMo/Switchyard)
- Cited upstream report: none found; a gap in the bare-name fallback added by
  [PR #384](https://github.com/NVIDIA-NeMo/Switchyard/pull/384)
- Target type: open-source/local
- Tested commit:
  [`ab7d26443899239632e97d9d9d8aedaa58c96b99`](https://github.com/NVIDIA-NeMo/Switchyard/commit/ab7d26443899239632e97d9d9d8aedaa58c96b99),
  current `main` on 2026-10-15
- Also tested release: `v0.3.0`
  ([`336196f6`](https://github.com/NVIDIA-NeMo/Switchyard/commit/336196f6)),
  the current tag
- Client dialect and endpoint: OpenAI Responses, `POST /v1/responses`, buffered
  and streamed
- Backend dialects: OpenAI Chat Completions and OpenAI Responses, deterministic
  local capture upstream
- Model: synthetic `model/chat`
- Relevant configuration: documented `passthrough` routes, `max_retries = 0`, no
  credentials

## Gate 1: correctness

### Exact reproduction

```text
git clone https://github.com/NVIDIA-NeMo/Switchyard.git
cd Switchyard
git checkout ab7d26443899239632e97d9d9d8aedaa58c96b99
cargo build --release -p switchyard-server
cd /path/to/kairo
transcripts/102/run_repro.sh /path/to/Switchyard/target/release/switchyard-server
transcripts/102/same_format_check.sh /path/to/Switchyard/target/release/switchyard-server
python3 transcripts/102/dispatch_check.py
```

- Expected behavior: a `function_call` for a tool the request declared at the
  top level comes back with no `namespace`, so the client dispatches the tool the
  model actually chose.
- Observed behavior: the call comes back as
  `{"type":"function_call","name":"search","namespace":"mcp__docs__"}`. Codex
  looks a tool up by the exact `{name, namespace}` pair, so the local tool runs
  against the MCP server. Switchyard returns HTTP 200 with no warning.
- Raw request and response evidence:
  [`capture-bug-buffered.jsonl`](../../transcripts/102/capture-bug-buffered.jsonl),
  [`capture-bug-stream.jsonl`](../../transcripts/102/capture-bug-stream.jsonl),
  and the same-format
  [`capture-same-format-bug.jsonl`](../../transcripts/102/capture-same-format-bug.jsonl)
- Forwarded-request evidence: [`forwarded.jsonl`](../../transcripts/102/forwarded.jsonl)
  and [`forwarded-same-format.jsonl`](../../transcripts/102/forwarded-same-format.jsonl),
  the exact UTF-8 bodies the backend read, each recording the name it called
- Reproduction rate: namespace stamped on the top-level tool 5/5 buffered and 5/5
  streamed on the Chat backend, and 5/5 on the Responses backend. Dropping the
  namespace container gives 0/5.
- Numbering: this finding was drafted as 095 and renumbered to 102 because 095
  was taken on `main`. The canary strings inside the committed captures, such as
  `NAMESPACE_CANARY_095_bug_buffered_1`, keep the draft number so the evidence
  bytes stay exactly as recorded.
- Release check: identical result on `v0.3.0` (`336196f6`), so this is not a
  change on `main` alone.
- Smallest isolated trigger: add one top-level tool whose name matches a tool
  inside exactly one namespace container. Removing either side removes the
  defect.

### The discriminating case

A hijack and a correct restore produce the **same dispatch-relevant bytes**. Both
`function_call` items are
`{"name":"search","namespace":"mcp__docs__"}`; only the enclosing response `id`
differs, and that difference is an artifact of the mock's id, not of the
translation. So the client request and the client response alone cannot tell
them apart, and every capture records `called_upstream_name` for the frozen
checker to read.

| Case | Tool set sent | Backend calls | Result |
|---|---|---|---|
| `bug` | top-level `search` + namespace `mcp__docs__`[search] | `search` | namespace stamped, hijack |
| `cross-qualified` | identical tool set | `mcp__docs____search` | namespace restored, **correct** |
| `control-qualified` | namespace only | `mcp__docs____search` | namespace restored, correct |
| `control-local-only` | top-level `search` only | `search` | no namespace, correct |

A checker that reads only the request and the response cannot tell `bug` from
`cross-qualified`, and would flag correct gateway output. See the independent
review below.

### Binary provenance

| Build | SHA-256 of `target/release/switchyard-server` |
|---|---|
| `ab7d2644` (`main`, 2026-10-15) | `5bc0b2ed9a840aaa934d1a56d3e148d8a6f3ffb0b8536027fad700987498e073` |
| `336196f6` (`v0.3.0`) | `c5e5ca66ddf59ddca7cb6f659d00ed80087a73976c9326c511f555b37c1f0c46` |

Both report `switchyard-server 0.3.0`; the crate version was not bumped on
`main` after the tag. An independent reviewer rebuilt both from scratch in a
separate clone and matched both hashes byte for byte.

### Hosted closed-source evidence

N/A. The real open-source release server ran locally from a pinned checkout.

### Control

```text
# Same server, same route, same backend, same tool definitions.
# bug / cross-qualified share the tool set and differ only in the called name.
# control-qualified and control-local-only remove one side of the collision.
```

- Control result: `cross-qualified` and `control-qualified` restore
  `name: search, namespace: mcp__docs__` 5/5 because the backend used the
  qualified name it was offered. `control-local-only` returns `name: search` with
  no namespace 5/5.
- Attribution: binary, route, tool definitions, prompt and backend response are
  identical across the pairs. Only the tool set or the called name changes.

| Rung | Result | Evidence |
|---|---|---|
| Direct provider | N/A, the defect is in the gateway's response rewrite | The backend never emits a `namespace`; it is added after the reply |
| Plain or bypass path | `cross-qualified` restores the correct pair 5/5 on the identical tool set | `capture-cross-qualified-buffered.jsonl` |
| Routed path | `control-local-only` 0/5 misroutes, `bug` 5/5, same server | `capture-control-local-only-buffered.jsonl`, `capture-bug-buffered.jsonl` |
| Trigger removed | Dropping the namespace container gives 0/5; dropping the top-level tool gives 0/5 | same files |

### False-positive checks

- [x] The backend was offered both tools as distinct upstream names and called
      the top-level one, so the model really did choose the local tool.
- [x] The backend refuses to name a tool it was not offered and never emits a
      `namespace` field, so the rig cannot manufacture a disagreement of its own.
- [x] The real `switchyard-server` public HTTP entry point produced the defect,
      not a library call.
- [x] Pinned current upstream `main` and confirmed the same result on the
      current `v0.3.0` release, so this is not version drift.
- [x] Model nondeterminism is absent; the backend echoes a canned call chosen by
      a canary.
- [x] The container name is an opaque prefix, so both Codex spellings reproduce.
      Codex sends `mcp__<server>` for an MCP server and `mcp__<server>__` for a
      connector-style toolset, and a builtin group such as `multi_agent_v1`
      carries no `mcp__` prefix at all. An independent reviewer reproduced 10/10
      with a builtin group and drove the real Codex 0.159.3 binary against a
      stdio MCP server named `mcp__reviewer`.
- [x] Evidence contains synthetic canaries and descriptions only.

## Gate 2: usefulness

### Who gets bitten

- Affected user: someone running Codex CLI, or any Responses client that sends
  Codex `namespace` containers, through a Switchyard route, with at least one
  namespace container configured.
- Real workflow: Codex sends its top-level tools and its `namespace` containers
  in the same `tools` array. A Codex request captured in
  [openai/codex#23186](https://github.com/openai/codex/issues/23186) shows
  `{"type":"function","name":"exec_command",...}`, more native tools, then
  `{"type":"namespace","name":"mcp__open_websearch__","tools":[...]}`, so the
  mixed shape is what a session produces. Codex's own shell tool is named
  `exec_command`, which is also a natural name for a shell-style MCP server.
- Preconditions and frequency: the trigger is a top-level tool whose name equals
  a tool inside exactly one namespace container. That is a configuration
  condition, not a universal one. Once it holds, every call to that tool is
  affected: measured 5/5 buffered, 5/5 streamed, and 5/5 on a same-format route.

### Observable consequence

- User action: the model calls the local tool, for example a local `search`.
- Wire defect: Switchyard adds `namespace: "mcp__docs__"` to the returned
  `function_call`, on the buffered body and on every streamed frame carrying the
  call.
- End-user failure: Codex looks up `{name, namespace}` and runs the MCP server's
  tool of that name instead. The local tool never runs, and a tool chosen for
  local execution executes against another system.
- Consumer boundary:
  [`dispatch_check.py`](../../transcripts/102/dispatch_check.py) replays the
  captured responses through Codex's documented dispatch rule, a plain tool name
  resolving in the top-level registry and a namespaced one inside its
  container, with no fallback. The expected target is derived from the recorded
  upstream call name, not from a per-case assumption. In 5/5 bug trials the call
  resolves to `REMOTE documentation search`; in all controls it resolves to the
  tool the backend actually called.
- Measured impact: 5/5 trials misroute on both response modes, on both backend
  dialects, and on both `main` and `v0.3.0`. No error, no warning, HTTP 200.
- Inferred impact: a namespaced tool with side effects, such as a file or ticket
  MCP server, would receive calls the model made for a local tool. That is an
  inference; this writeup did not execute a real MCP server.

### Bug or not

- Expected behavior: yes. The module that does the rewrite states the rule it
  must not break. `codex_namespaces.rs:84-89` says fallbacks are registered
  "when neither can be confused with another tool", and
  `switchyard_protocol::codex_namespaces::split_qualified_name` returns `None`
  for a name the request never qualified "so an unrecognized call is left alone
  rather than attributed to the wrong namespace". The project's own test
  `leaves_an_ambiguous_bare_name_alone` at `codex_namespaces.rs:217` encodes
  the same rule and gives the reason: "a wrong guess dispatches the call to the
  wrong server." A top-level tool is another owner of the name, so the guard is
  narrower than the invariant it documents. Code and test, not prose, are the
  spec here.
- Maintainer ruling searched: none. No commit, issue, PR, test or doc classifies
  this as intended. The closest artifact is PR #831, which reads the same map
  for routing and treats the pair as authoritative, and PR #384, which
  introduced the bare fallback without covering a top-level claimant.
- Trigger is supported usage: `namespace` is Codex's documented tool shape and
  `CHANGELOG.md` lists namespace preservation as shipped behaviour. A request
  holding both shapes is what Codex sends. Nothing here is a secret in a
  non-secret field or a disabled security control.
- Real boundary crossed: yes. The gateway decides which of two distinct tools
  executes. The forwarded bytes prove the two tools stayed distinct upstream,
  with different descriptions, and the response attributed the local call to the
  remote one.
- Fix in one sentence: pass the top-level tool names into
  `qualified_tool_origins` and skip the bare-name fallback for any name a
  top-level tool owns, the same way `qualified_names.contains` already skips
  names that are real qualified tools.
- Label: `bug`

## Gate 3: upstream status

- Date checked: 2026-10-15
- Upstream commits checked: `ab7d2644` (current `main`) and `336196f6` (`v0.3.0`)
- Search terms: `namespace`, `MCP namespace`, `tool namespace`, `function_call
  namespace`, `mcp__`, `bare tool name`, `dispatch wrong server`, `codex
  namespace`, `top-level tool namespace`, `hijack`, `bare fallback`,
  `collision tool name`, `attributed to the wrong namespace`
- Issues searched: all open and closed issues matching those terms, including
  #515, #832, #502, #121, #60, #51, #47. No match. #515 is Codex tools dropped
  on the request path, the opposite direction and closed.
- Pull requests searched: #384, #516, #749, #831, #643, #875 and a
  repository-wide search. #384 added the bare fallback. #831 reads the same map
  for routing and does not touch the response rewrite.
- Releases, changelog and docs: `v0.3.0` and `v0.3.0-rc.*`, `CHANGELOG.md`
  ("Codex MCP namespaces preserved through translation"), and
  `crates/protocol/src/codex_namespaces.rs`
- Relevant commits: `c7beccd4` (PR #384, added the bare-name fallback),
  `42784fdc` (PR #831, moved the map to the protocol crate for routing)
- An independent reviewer re-fetched `main` on the review date and confirmed it
  had not moved past `ab7d2644`

Classification:

- [x] Novel
- [ ] Duplicate, open and still reproducible
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

Present in the current `v0.3.0` release and in current `main`, with no upstream
artifact covering a top-level tool claiming a namespaced tool's bare name.

## Root cause

`crates/switchyard-translation/src/codex_namespaces.rs:96` builds `split` from
the request's namespace map alone, and `bare_claims` at line 106 counts how many
namespaces claim each bare tool name. The bare-name fallback at line 124 fires
when that count is exactly one.

The map is only written from the `type == "namespace"` branch at
`codecs/responses/buffered.rs:1029`, through `record_tool_namespace` at line
1046. A top-level `function` tool is decoded at lines 1079 to 1106 and is never
recorded, so it is invisible to `bare_claims`.

With a top-level `search` and a namespace holding its own `search`, the count
is one, the fallback registers `search` to `mcp__docs__`, and
`restore_qualified_tool_names` at `codex_namespaces.rs:140` writes that
namespace onto the returned call at lines 157 to 160. Both the buffered path
(`engine.rs:220`) and the streamed path (`helpers.rs:155`) take this route. The
same-format Responses path shares the codec, which is why it reproduces there
too.

## Frozen invariant

- Issue writeup: this file
- Checker: `codex_namespace_only_applies_to_namespaced_tools` in
  `crates/harness/src/checks.rs`
- Conformance tests: `switchyard_codex_namespace_hijacks_a_top_level_tool`,
  `switchyard_codex_namespace_same_tool_set_valid_restore_is_conformant`,
  `switchyard_codex_namespace_trigger_removed_controls_are_conformant`,
  `switchyard_codex_namespace_forwarded_bytes_offered_two_distinct_tools`,
  `switchyard_codex_namespace_checker_rejects_evidence_without_the_upstream_call`,
  and `switchyard_codex_namespace_hijack_is_not_confined_to_cross_format` in
  `crates/harness/tests/conformance.rs`
- Why invariant-level: the checker reads the recorded client request, the
  recorded upstream call name and the recorded client response. It names no
  Switchyard function, and it refuses evidence that omits the upstream call
  rather than guessing.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `transcripts/102/run_repro.sh SWITCHYARD_BIN` | PASS, bug 5/5 per mode |
| Same-format route | `transcripts/102/same_format_check.sh SWITCHYARD_BIN` | PASS, bug 5/5 |
| Release cross-check | both scripts against `v0.3.0` | PASS, bug 5/5 per mode |
| Consumer boundary | `python3 transcripts/102/dispatch_check.py` | PASS, 5/5 misrouted, every control 0/5 |
| Harness | `cargo test --workspace` | PASS; this finding contributes 2 unit and 6 conformance tests |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS |
| Independent review | `.github/agents/kairo-reproduction-reviewer.agent.md` | ACCEPT after one round of fixes, see below |

## Security and scope

- [x] No API key, credential, private prompt or unsanitized provider response is
      committed. The rig needs no credential.
- [x] The backend is a local deterministic capture, not a live provider.
- [x] The finding is one defect.
- [x] Only local loopback ports were used.
- [x] The two pinned Switchyard checkouts live under the gitignored
      `tools/switchyard/` path, so no clone can be committed by accident.
- [x] Unrelated working tree changes are excluded.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS
- Overall: ACCEPT

## Independent review

### Round 1: REJECT

On 2026-10-15 an independent reviewer followed
`.github/agents/kairo-reproduction-reviewer.agent.md` and rejected the pull
request. Correctness, usefulness and upstream status all passed. The repository
gate failed on the frozen checker.

- Binary provenance: the reviewer cloned Switchyard separately, rebuilt both
  `ab7d2644` and `336196f6` from scratch with cold target directories, and
  matched both claimed SHA-256 values byte for byte.
- Reproduction: the reviewer wrote its own rig with its own request shapes and
  reproduced 5/5 on both builds, and confirmed all committed capture files were
  byte-identical to an independent rerun.
- Attribution attacks: plain tool removed 0/5, namespace removed 0/5,
  qualified name called restores correctly, two namespaces claiming the same
  bare name correctly suppress the fallback, and a non-MCP namespace
  (`multi_agent_v1`) collides the same way. Root cause re-derived from source,
  line for line.
- Useful scope widening: the defect is **not** confined to the cross-dialect
  route, and the reviewer drove the real `codex-cli 0.159.3` binary against the
  server with a stdio MCP server exposing `exec_command`, observing the MCP
  server receive 2363 `tools/call` requests while the built-in shell never ran.
- Blocking finding: the checker judged the request and response alone, so it
  flagged the **correct** restore of a namespaced tool as a violation. The
  original `control-qualified` passed only because it deleted the top-level tool,
  which removed the exact configuration under test. The checker would have
  frozen an invariant that a correct Switchyard build violates.

### Round 2: ACCEPT

The reviewer rebuilt both pinned commits from a fresh clone, matched both
SHA-256 values byte for byte, reproduced the bug 5/5 on its own build and 10/10
with its own rig using different tool names, and found the committed captures
byte-identical to an independent rerun. It then exercised the real checker from a
scratch crate with a path dependency on this repository's harness.

Fixes applied in response to round 1:

1. The checker now requires `called_upstream_name` on every record and uses it.
   A namespace is a violation only when the backend called the bare name of a
   tool the request also declared at the top level, or when the returned pair was
   never declared. Evidence without the upstream call is refused, not guessed.
2. New `cross-qualified` case: the identical tool set with the backend calling
   the qualified name, which must be conformant. This is the pair round 1
   required and the old control could not express.
3. Every capture now records `called_upstream_name`, taken from the forwarded
   body the backend read, and the rig now requires a canary to match exactly one
   forwarded record so a response cannot be paired with another request's call.
4. The reviewer's scope point was verified rather than asserted: a
   Responses-to-Responses passthrough route is now in the rig
   (`same_format_check.sh`) and reproduces 5/5, with its own controls and its own
   conformance test.
5. The two pinned checkouts were moved under the gitignored `tools/switchyard/`
   path so no clone can be committed. Both binaries were rebuilt and re-verified
   after the move; both SHA-256 values are unchanged.

Reviewer verification of the fix, on the real checker:

| Case | Verdict |
|---|---|
| Both shapes, backend called bare `search` | Violation |
| Identical request and identical response bytes, backend called the qualified name | Conformant |
| Namespace only, qualified call | Conformant |
| Top-level only, bare call, no namespace | Conformant |
| Two namespaces plus a top-level tool, bare call | Violation |
| Returned pair never declared | Violation |

It also flipped `called_upstream_name` alone in each committed fixture and
confirmed the verdict follows it, corrupted each capture in turn in a copy
outside the repository and confirmed each corruption flips exactly the test that
owns that evidence, and found no false positive across five adversarial probes
including nested namespace containers and a chat-shaped top-level tool.

Measured with the real client, not a model of it: the reviewer drove
`codex-cli 0.159.3` against its own build with a stdio MCP server exposing a
tool named `exec_command`. Codex's built-in shell never ran, and the MCP server
received 4155 `tools/call` requests. With the namespace container renamed to
`zzz_probe`, the same binary, server and upstream reply ran the local handler and
the MCP server received 0 calls.

Reviewer verdict after fixes: ACCEPT, with no blocking findings. Its
non-blocking observations were addressed as follows: the `assert_is_empty` lint
landmine on clippy 1.99 was fixed, the overstated container-spelling claim was
corrected to a prefix claim backed by the real capture, the canary collision was
removed, and the checker's two bounded false negatives are now stated in its doc
comment.