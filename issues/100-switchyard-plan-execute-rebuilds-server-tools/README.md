# 100, Switchyard's plan_execute route rebuilds an Anthropic server tool as an empty-schema custom tool, so Anthropic rejects the request and Claude Code's WebSearch fails on every planning turn

- **Upstream**: [NVIDIA-NeMo/Switchyard](https://github.com/NVIDIA-NeMo/Switchyard).
  The root cause is known to the maintainers. Maintainer PR
  [#958](https://github.com/NVIDIA-NeMo/Switchyard/pull/958), which cites internal
  ticket SWITCH-1487, says "Reconstruction dropped `mcp_servers`, rebuilt provider
  tools as custom functions". It was **closed unmerged on 2026-10-13**. Its
  protocol half landed as
  [#993](https://github.com/NVIDIA-NeMo/Switchyard/pull/993), merged 2026-10-14 as
  `ab7d2644`, whose description says "Changes to the translation crate are planned
  for a followup PR". Open PR
  [#917](https://github.com/NVIDIA-NeMo/Switchyard/pull/917) covers the cross-format
  case only and assumes same-format replay always keeps server tools. No public
  issue or pull request names the plan_execute trigger or Claude Code's WebSearch.
- **Tool under test**: `switchyard-server` built from `main` at
  `ab7d26443899239632e97d9d9d8aedaa58c96b99` (2026-10-14, the #993 merge) and from
  release `v0.3.0` at `336196f6fbfc97ddc71c1700f6092e564e9f23c2`, both clean
  checkouts built with Rust 1.99. Route `POST /v1/messages`, Anthropic Messages in
  and out (`format = "anthropic_messages"`), live `api.anthropic.com` behind a
  recording relay. Targets `claude-sonnet-5-5` (capable) and `claude-haiku-5-5`
  (efficient). Configuration: `transcripts/100/rig/config.toml`, the documented
  plan_execute example with Anthropic targets, plus a passthrough control route on
  the same server.
- **Consumer under test**: Claude Code 2.1.289 in print mode, pointed at Switchyard
  with `ANTHROPIC_BASE_URL`, as in Switchyard's own Claude Code recipe.
- **Reproduced**: 2026-10-15 UTC, macOS arm64. **5 of 5** per case on both builds,
  plus 6 of 6 Claude Code runs per route in the exploratory session before the
  recorded matrix.
- **Label**: `bug`.

## What breaks

Plan/execute is one of Switchyard's listed routing strategies. It keeps a coding
agent on the capable model while it inspects and plans, then hands the session to
the efficient model at the first edit. To make the capable model plan, it prepends
a planning instruction to every planning-phase request.

Claude Code's WebSearch tool works by sending a separate Messages request that
declares Anthropic's server-side search tool:

```json
"tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]
```

Anthropic runs that tool itself, so it has no `input_schema`. Through a
plan_execute route, Switchyard forwards it as a client function with an empty
schema instead:

```json
"tools": [{"name": "web_search", "description": "", "input_schema": {}}]
```

Anthropic rejects that request with `400 tools.0.custom.input_schema.type: Field
required`. Claude Code turns the 400 into an error tool result, and the agent
reports it has no search results:

```text
The WebSearch call failed with an API error (`400 tools.0.custom.input_schema.type: Field required`), so I have no search result to cite and can't say what the latest stable Rust release is.
```

That is every web search during the planning phase, which is when an agent
researches an unfamiliar library, error, or API before editing. Nothing in the
plan_execute configuration avoids it: `planning_prompt` cannot be empty
(`crates/libsy/src/algorithms/plan_execute.rs:66`), and the prompt is what
triggers the rebuild. The same Claude Code session on a passthrough route to the
same model searches normally.

## Wire evidence

All paths below are under `transcripts/100/capture/main-ab7d2644/`. Each case has
a `client/` hop (client to Switchyard) and an `upstream/` hop (Switchyard to
Anthropic), recorded by `rig/relay.py` on both sides of the server.

**Smallest trigger**, `minimal/sy-plan-01/`. The client request, verbatim:

```text
POST /v1/messages HTTP/1.1
Host: 127.0.0.1:9100
Accept-Encoding: identity
Content-Length: 231
content-type: application/json
anthropic-version: 2023-06-01

{"model": "sy-plan", "max_tokens": 400, "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}], "messages": [{"role": "user", "content": "Search the web for the latest stable Rust version. One sentence."}]}
```

What Switchyard forwarded (`upstream/001-request.http`). The planning instruction
is the system prompt, and the server tool has become a custom tool:

```text
POST /v1/messages HTTP/1.1
content-type: application/json
anthropic-version: 2023-06-01
x-api-key: [REDACTED: credential]
accept: */*
host: 127.0.0.1:9101
content-length: 549

{"model":"claude-sonnet-5-5","system":"You are in the planning phase. Inspect the task and relevant code, then form a concrete implementation plan before modifying any files. Use read-only tools as needed. Do not edit until the plan is complete. Your first edit hands execution to another model.","messages":[{"role":"user","content":[{"type":"text","text":"Search the web for the latest stable Rust version. One sentence.","cache_control":{"type":"ephemeral"}}]}],"tools":[{"name":"web_search","description":"","input_schema":{}}],"max_tokens":400}
```

Anthropic's answer (`upstream/001-response.http`, tenant headers redacted), which
Switchyard returns to the client as a 400 with the same message:

```text
HTTP/1.1 400 Bad Request
...
{"type":"error","error":{"type":"invalid_request_error","message":"tools.0.custom.input_schema.type: Field required"},"request_id":"req_011Cg3HCs1qR1jKaqEkmTKwd"}
```

**Control, trigger removed**, `minimal/sy-pass-01/`: the same bytes to the
passthrough route on the same server and target. Exact replay survives and the
tool is forwarded unchanged, and the client gets 200 with a
`web_search_tool_result`:

```text
{"model":"claude-sonnet-5-5","max_tokens":400,"tools":[{"type":"web_search_20250305","name":"web_search","max_uses":1}],"messages":[...]}
```

**Provider control, no gateway**, `direct/`: the server tool sent straight to
Anthropic returns 200 and searches (`server-tool-NN`), and the exact tool
Switchyard forwards returns the same 400 (`reencoded-tool-NN`). The provider
accepts what the client sent and rejects only what Switchyard sent.

**Consumer**, `claude/sy-plan-01/`. Claude Code's WebSearch sub-request is
`client/003-request.http` (the tool exactly as above, with `max_uses: 8`), and
`upstream/003-request.http` carries the rebuilt tool. The 400 comes back, and
Claude Code's next main-loop request (`client/004-request.http`) hands the model:

```json
{"type": "tool_result", "content": "API Error: 400 tools.0.custom.input_schema.type: Field required", "is_error": true, "tool_use_id": "toolu_01TotM2KQCwMCjGbWdRQr1Xx", "cache_control": {"type": "ephemeral"}}
```

`claude/sy-pass-01/` is the same Claude Code task on the passthrough route. The
sub-request is forwarded unchanged, Anthropic runs the search, and the tool result
carries the results.

The rebuild also changes other fields of the WebSearch sub-request: the
forwarded body adds `"thinking": {"type": "adaptive"}`, which Claude Code did not
send, and joins Claude Code's three system blocks into one string. Neither causes
the failure. They are recorded here because they show the request is rebuilt, not
replayed.

**Matrix.** `results.json` in each capture directory records every run. The
runs per case on both builds:

| Case | main `ab7d2644` | v0.3.0 |
|---|---|---|
| Direct, server tool: 200 with search results | 5/5 | 5/5 |
| Direct, rebuilt tool: 400 | 5/5 | 5/5 |
| plan_execute, minimal: forwarded as custom tool, 400 | 5/5 | 5/5 |
| passthrough, minimal: forwarded unchanged, 200 | 5/5 | 5/5 |
| plan_execute, Claude Code: WebSearch rejected, agent reports the 400 | 5/5 | not run |
| passthrough, Claude Code: WebSearch runs, results returned | 5/5 | not run |

Redactions, all marked inline: the API key (`[REDACTED: credential]`),
Anthropic's `anthropic-organization-id` and `anthropic-workspace-id`
(`[REDACTED: tenant id]`), and the Claude Code device id inside
`metadata.user_id` (`[REDACTED: device id]`). None of them is in the disputed
bytes.

## Root cause

Three pieces of the current source combine, all at `ab7d2644`.

1. Plan/execute adds its planning prompt on every planning turn,
   `crates/libsy/src/algorithms/plan_execute.rs:181`:

   ```rust
   Phase::Plan => {
       prepend_system_prompt(&mut request, &self.config.planning_prompt);
   ```

2. Adding the prompt gives up exact replay,
   `crates/libsy/src/algorithms/util/prompts.rs:50-66`. `drop_exact_replay` clears
   `preservation.requests`, so the Anthropic codec stops replaying the body the
   client sent (`crates/switchyard-translation/src/codecs/anthropic/buffered.rs:208-215`)
   and encodes the normalized request instead. The comment above it gives the
   reason: without this, a router addition "never reaches the model".

3. The normalized request cannot represent a server tool.
   `decode_anthropic_tools`
   (`crates/switchyard-translation/src/codecs/anthropic/buffered.rs:789-811`) keeps
   every entry with a `name` as a function definition, drops `type` and every
   other field, and defaults the missing `input_schema` to `{}` (line 806).
   `encode_anthropic_tools` (line 1212) writes it back as
   `{"name","description","input_schema"}`, which Anthropic validates as a custom
   tool.

So any request that keeps exact replay forwards the server tool correctly, and
any request a router edits loses it. On plan_execute that is every planning
turn. Two other documented features take the same `drop_exact_replay` path:
Stage `handoff_notes` (through `append_note`) and the plan_execute
`handoff_prompt` and `planner_reasoning_as_text` options. They were read in the
source and not run here.

**Fix a maintainer would ship.** Keep Anthropic server tools as provider tool
definitions in the normalized request and re-emit them verbatim when the target
is an Anthropic Messages client. That is the translation follow-up #993 describes
and the reconstruction fix in #958. A narrower fix is to make plan_execute insert
its prompt into the preserved Anthropic body, the way
`prepare_request_for_target` already does for target `system_prompt` values
(`crates/switchyard-translation/src/util.rs:331`, with the Anthropic case in
`prepend_anthropic_system` at line 417).

## Bug or not

- **Is the expected behavior really the spec?** Yes. Same-format Anthropic traffic
  is meant to reach Anthropic unchanged apart from the router's addition. The
  codec's exact-replay path exists to keep "a same-format hop lossless"
  (`prompts.rs:39`). Open PR #917's test asserts that same-format Anthropic encoding
  keeps the server tool. The changelog records the maintainers fixing other losses
  on the rebuilt path, for example "encrypted reasoning items survive
  decode/encode after exact replay is invalidated" (#645). Plan/execute targets
  Claude Code explicitly: its mutation list names Claude Code's `Edit`,
  `MultiEdit`, and `NotebookEdit` tools
  (`crates/libsy/src/algorithms/util/tool_signals.rs:104-106`).
- **Have maintainers already ruled on it?** Yes, in favor of this report: #958
  calls rebuilding provider tools as custom functions a defect and set out to fix
  it. Nothing classifies server tools as unsupported on Anthropic-to-Anthropic
  routes.
- **Is the trigger supported usage?** Yes. `type = "plan_execute"` is a listed,
  non-experimental strategy in `docs/routing_algorithms/overview.md`. The rig uses
  its documented example with `anthropic_messages` targets and `api_key_env`.
  The client is unmodified Claude Code, and the tool is Anthropic's documented
  web search tool. The `--plan-execute` launcher flag is deprecated in the
  changelog, but the route type is not.
- **Is a real boundary crossed?** Not a disclosure claim. The consequence is a
  failed tool call: a request the provider accepts is turned into one it rejects.
- **What fix would a maintainer ship?** Preserve Anthropic server tool
  definitions through same-format reconstruction. That is code, not a doc note.

## Upstream status

Searched 2026-10-15 UTC in `NVIDIA-NeMo/Switchyard` with `gh search issues` and
`gh search prs` on: `web_search`, `server tool`, `server_tool`, `input_schema`,
`input_schema.type`, `tools.0.custom`, `web_search_20250305`, `plan_execute
anthropic`, `plan_execute web search`, `planning prompt`, `planning prompt server
tool`, `exact replay`, `drop_exact_replay`, `same-format replay tools`,
`handoff_notes`, `Claude Code WebSearch`. Also read `CHANGELOG.md`, `docs/`, and
`git log --since=2026-09-01` on main for the same terms.

- **#958** (closed unmerged 2026-10-13, maintainer, SWITCH-1487): "Reconstruction
  dropped `mcp_servers`, rebuilt provider tools as custom functions". This is the
  root cause above. The PR describes no trigger and no consumer failure.
- **#993** (merged 2026-10-14, `ab7d2644`): protocol types for MCP and provider
  tools, "Changes to the translation crate are planned for a followup PR". The
  tested main is this merge, and the defect is present on it.
- **#917** (open, review required): drops server tools from the normalized list so
  they do not reach OpenAI-format upstreams. On the rebuilt Anthropic path that
  would remove web search silently instead of causing a 400, so it does not fix
  this.
- No issue or pull request mentions plan_execute, Claude Code's WebSearch, or the
  `tools.0.custom.input_schema.type` rejection.

Classification: **duplicate-open**. The root cause is tracked internally
(SWITCH-1487) and its fix is in progress. It is still live on current main and on
the latest release v0.3.0.

What this adds: the trigger that makes it hit on every planning turn of a
standard route, the consumer failure in an unmodified Claude Code, live provider
evidence for both hops, and a checker that a fix can be tested against.

## Test

`crates/harness/src/checks.rs`, `anthropic_server_tools_forwarded`. The invariant
is stated as a property of the wire, not of this code path: every Anthropic
server tool in the client body (a `tools[]` entry whose `type` is anything other
than `custom`) must appear in the forwarded body with an equal JSON value. Key
order is ignored, custom tools are not compared, and a client body without a
server tool is a violation, so a capture that lost the tool cannot pass
vacuously. It does not depend on how a gateway rebuilds the tool: dropping it,
stripping `type`, or losing `max_uses` all fail.

`crates/harness/tests/conformance.rs` asserts both directions on the frozen bytes:

- `switchyard_plan_execute_rebuilds_the_web_search_server_tool`: `Violation` on all
  10 plan_execute minimal runs (5 on main, 5 on v0.3.0), naming the empty
  `input_schema`, with 400 on both hops.
- `switchyard_passthrough_forwards_the_web_search_server_tool_unchanged`:
  `Conformant` on all 10 passthrough runs, with 200 and a search result.
- `anthropic_rejects_the_tool_switchyard_forwards_and_accepts_the_original`: the
  no-gateway control, 5 of 5 each way.
- `claude_code_websearch_fails_only_on_the_plan_execute_route`: the Claude Code
  WebSearch sub-request is a `Violation` on all 5 plan runs, with Claude Code's
  output reporting the 400, and `Conformant` with search results on all 5
  passthrough runs.
- `switchyard_server_tool_matrix_matches_the_recorded_runs`: both
  `results.json` files are pinned to their commits and clean trees and record
  5 of 5 per case.
- Three unit tests in `checks.rs` cover reordered keys alongside a custom tool, a
  rebuilt, missing, or partially stripped server tool, and a capture with no
  server tool or no HTTP framing.

## Reproduce

See `transcripts/100/rig/build-and-run.md` for the build. Then, with
`ANTHROPIC_API_KEY` set:

```bash
python3 transcripts/100/rig/reproduce.py \
  --switchyard-bin /path/to/sy-main/target/release/switchyard-server \
  --switchyard-src /path/to/sy-main \
  --output /tmp/kairo-100-main --runs 5 --claude
```

Expected output, after the identity line:

```text
{"direct_server_tool_ok": 5, "direct_reencoded_tool_400": 5, "plan_forwarded_custom_tool_400": 5, "pass_forwarded_server_tool_ok": 5, "runs_per_case": 5, "claude_plan_websearch_failed": 5, "claude_pass_websearch_ok": 5}
```

The script checks out nothing itself. It records the checkout's commit and whether
its tree is clean, exits non-zero if any case differs from the claim, and refuses
to finish if the key appears in any output file. The frozen bytes are checked by
the harness:

```bash
cargo test --workspace
python3 tools/update-readme-counts.py --check
```

## Limitations

- Only `web_search_20250305` was run. Other Anthropic server tools (web fetch,
  code execution, tool search) and `mcp_servers` take the same decode path, but
  were not sent.
- Only plan_execute's planning phase was run. Stage `handoff_notes`, plan_execute's
  `handoff_prompt` and `planner_reasoning_as_text`, and the advisor redo path call
  `drop_exact_replay` too, but they were read in the source, not exercised. A
  session that has already made its first edit is in the execute phase, gets no
  prompt, and should keep exact replay. That was also not exercised.
- Claude Code with an API key through `api_key_env`. The documented Claude Code
  recipe uses `forward_auth` with a Claude subscription login, which was not
  tested. The rebuilt tool list is independent of the credential mode.
- One provider account, recorded 2026-10-15 from 03:03:14 to 03:05:31 UTC (main)
  and 03:05:48 to 03:06:30 UTC (v0.3.0), by the `Date` headers of Anthropic's
  responses. The
  model's decision to call WebSearch is nondeterministic, but it called it in all
  10 recorded and 12 exploratory Claude Code runs. The claim is about the
  forwarded tool definition and the provider's status code, which do not vary.
- Claude Code cases were recorded on main only. v0.3.0 was run with the direct and
  minimal cases, which exercise the same server path.
- macOS arm64 only.
