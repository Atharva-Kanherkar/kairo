# 093, Bifrost's MCP gateway drops `isError: true` from tool results, so a tool call the upstream MCP server reported as FAILED reaches the MCP client as a SUCCESS

- **Upstream**: [maximhq/bifrost](https://github.com/maximhq/bifrost).
  [#7612](https://github.com/maximhq/bifrost/issues/7612) "[Bug]: MCP gateway drops `isError: true` from tool results",
  OPEN since 2026-09-27, zero comments as of 2026-10-01. Fix PR
  [#7640](https://github.com/maximhq/bifrost/pull/7640) "fix(mcp): keep the upstream isError flag on gateway tool results",
  OPEN and **not merged** as of 2026-10-01, head `862b3bea5cd8a940d52fefdfb57a0741efc48c84`.
  Searched `MCP isError`, `MCP gateway drops isError`, `isError tool result`,
  `virtual MCP endpoint tool error`, `/mcp tools/call error flag`, `NewToolResultText isError`,
  `toolmanager is_error`, `keep isError flag`, and `flatten tool results MCP`.
  Adjacent and deliberately **not** absorbed:
  [#7666](https://github.com/maximhq/bifrost/issues/7666) reports the same endpoint
  flattening all non-text content and dropping `structuredContent`. That is a
  different and broader defect, and the same gateway rebuilds its reply in the
  same helper, so it is tracked separately.
- **Tool under test**: `maximhq/bifrost` release
  [`transports/v2.2.4`](https://github.com/maximhq/bifrost/releases/tag/transports/v2.2.4),
  commit `ed8371a9779bfbc8aa689d4d77964cf8ce9308bf`, `core` v1.11.0.
  Built from source with `go1.26.1 darwin/arm64` because the Docker daemon was
  unavailable in this environment. The binary is sha256
  `359f13b4b267a1313252a6f2ea675b692ea575877379abd91137e2ffb98e0efb`.
  The defective line is **byte-identical on current `main`** at `f790ef25b13385d65f155145ed2729844e51ea07`
  and `transports/v2.2.4` is still the current HTTP release.
- **Consumer and upstream SDK under test**: `github.com/mark3labs/mcp-go`
  **v0.43.2**, the version the target vendors. Used three ways: as the upstream
  MCP server, as the direct stdio client, and as the HTTP MCP client at the
  consumer boundary.
- **Reproduced**: 2026-10-01, macOS arm64, keyless, fully offline on localhost.
  Four cells, **5 of 5 each**, `failures: []` in `transcripts/093/results.json`.
  No model, no clock, no randomness, and a constant upstream payload.
- **Label**: `bug`.

## What breaks

An MCP client calls a tool through Bifrost's own MCP endpoint at `POST /mcp`.
Bifrost proxies the call to a configured MCP server. When that server reports
the tool **failed**, Bifrost executes it, hands the client the failure text, and
drops the `isError` flag that said the call failed.

The client therefore receives a well-formed, HTTP 200, MCP-conformant tool
result whose content is the error text and whose failure flag reads false.
An MCP client that branches on the flag, which is what the spec tells it to do,
concludes the tool succeeded.

Why an absent key is worse than a wrong one: per the MCP specification revision
2026-07-28, [server/tools, "Error Handling"](https://modelcontextprotocol.io/specification/2026-07-28/server/tools),
tools have exactly two error mechanisms, a JSON-RPC protocol error and a tool
result carrying `isError: true`, and "clients SHOULD provide tool execution
errors to language models to enable self-correction." The receiving SDK says the
same in its own type, `github.com/mark3labs/mcp-go` v0.43.2
`mcp/tools.go:47-50`:

```go
	// Whether the tool call ended in an error.
	//
	// If not set, this is assumed to be false (the call was successful).
	IsError bool `json:"isError,omitempty"`
```

So the absence of the key is not neutral. It is actively read as success.

The affected workflow is any agent loop that drives tools through Bifrost's MCP
endpoint and decides whether to retry, escalate, or report based on the result's
failure flag. Concretely: an agent asks a tool to charge a card, write a row, or
call a deployment API; the tool fails; the agent reads SUCCESS; the agent tells
the user the operation completed; the user is told something that did not happen.
Because the error text still arrives, an agent that also pattern-matches the
text will often notice, which is what makes this a correctness bug rather than a
data-loss bug: the failure is present but unlabelled, so only clients that rely
solely on the flag are wrong, and those are the clients the spec is written for.

Frequency: **100 percent, 5 of 5, deterministic.** Every failed tool call
through `POST /mcp` on `transports/v2.2.4` loses the flag, because the loss is a
single unconditional constructor call rather than a race or a shape-dependent
branch. The one precondition is that the upstream MCP server reports failure via
`isError`, which is the spec's mechanism and what the SDK's own
`NewToolResultError` produces.

## Wire evidence

Three bytes, one call. All are verbatim from
`transcripts/093/cells/violation_gateway_iserror/runs/run-01/`.

### `upstream.http`, what the MCP server actually put on the stdio pipe

`upstream-stdio-raw-probe.log`, recorded by a transparent byte-logging relay
between Bifrost and the MCP server:

```text
<<<client-to-server>>>{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"kairofail-always_fails","arguments":{}}}
<<<server-to-client>>>{"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\"code\":\"KAIRO_FAIL\",\"detail\":\"deterministic upstream failure\"}"}],"isError":true}}
```

The failure flag **arrives at the gateway**. This is the precondition the whole
finding rests on, and it is why the loss is attributable to the gateway rather
than to the upstream server or to a version skew.

### `observed.http`, what the gateway emitted to the client

`client-response.http`, captured off a real fasthttp socket by a second
transparent relay, so these are socket bytes and not an HTTP client
reconstruction. The request was a plain hand-written JSON-RPC POST:

```text
POST /mcp HTTP/1.1
Host: 127.0.0.1:62861
Content-Type: application/json
Accept: application/json, text/event-stream
Content-Length: 114
Connection: close

{"id":1,"jsonrpc":"2.0","method":"tools/call","params":{"arguments":{},"name":"KairoFail-kairofail-always_fails"}}
```

```text
HTTP/1.1 200 OK
Server: fasthttp
Content-Type: application/json
Content-Length: 144

{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{\"code\":\"KAIRO_FAIL\",\"detail\":\"deterministic upstream failure\"}"}]}}
```

`isError` is **absent**. HTTP 200, content intact, only the flag gone.

### `expected.http`, what a lossless pipe must emit

The same gateway, same request, same payload, with the flag preserved. Measured
on PR #7640 head `862b3bea5cd8a940d52fefdfb57a0741efc48c84`, from
`transcripts/093/cells/differential_pr7640/runs/run-01/client-response.http`:

```text
{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{\"code\":\"KAIRO_FAIL\",\"detail\":\"deterministic upstream failure\"}"}],"isError":true}}
```

Content-Length 159 against the violating cell's 144. The **only** difference
between the two responses is `,"isError":true`.

### The cell matrix

All four cells, five runs each, every run's full artifacts retained. The
`isError` column is **key presence**, not value, because the SDK omits the key
when it is false and so absent and false are the same wire fact. One tool
execution per call, counted by the MCP server's own ledger rather than inferred.

| Cell | `isError` on client wire | real mcp-go consumer classified | upstream emitted `isError: true` | Rate |
|---|---|---|---|---|
| `violation_gateway_iserror` | **absent** | **SUCCESS** | true | 5 of 5 |
| `control_direct_stdio` | present, true | FAILURE | true | 5 of 5 |
| `control_gateway_success` | absent (correct) | SUCCESS | false | 5 of 5 |
| `differential_pr7640` | present, true | FAILURE | true | 5 of 5 |

The single-variable pairs:

- **violation vs `control_direct_stdio`**: identical request, identical tool,
  identical upstream bytes. The only difference is whether Bifrost is in the
  path. Removing the gateway restores the flag.
- **violation vs `control_gateway_success`**: identical route, identical
  gateway build, identical handler shape. The only difference is which of the two
  tools the MCP server answers, and those two handlers differ only in the result
  constructor they call. This is what rules out "the endpoint never emits
  `isError`": the endpoint emits it, correctly, for the tool that did not fail.
- **violation vs `differential_pr7640`**: identical everything except the
  gateway build.

### Consumer boundary, measured not inferred

A real `mcp-go` streamable-HTTP MCP client against the live gateway at
`http://127.0.0.1:62861/mcp`,
`transcripts/093/cells/violation_gateway_iserror/runs/run-01/consumer-stdout.txt`:

```json
{"base_url":"http://127.0.0.1:62861/mcp","consumer_classifies":"SUCCESS","content_texts":["{\"code\":\"KAIRO_FAIL\",\"detail\":\"deterministic upstream failure\"}"],"decoded_is_error":false,"tool":"KairoFail-kairofail-always_fails","wire_has_is_error":false,"wire_result_verbatim":"{\"content\":[{\"type\":\"text\",\"text\":\"{\\\"code\\\":\\\"KAIRO_FAIL\\\",\\\"detail\\\":\\\"deterministic upstream failure\\\"}\"}]}"}
```

The same SDK on the same MCP server with no gateway,
`transcripts/093/cells/control_direct_stdio/runs/run-01/client-stdout.txt`:

```json
{"consumer_classifies":"FAILURE","decoded_is_error":true,"tool":"kairofail-always_fails","wire_has_is_error":true,"wire_is_error_value":true,"wire_result_verbatim":"{\"content\":[{\"type\":\"text\",\"text\":\"{\\\"code\\\":\\\"KAIRO_FAIL\\\",\\\"detail\\\":\\\"deterministic upstream failure\\\"}\"}],\"isError\":true}"}
```

The client is not raising, not retrying, and not erroring. It is confidently
reporting SUCCESS. That is the user-visible failure: an agent behind this
gateway is told its tool succeeded.

### The SDK is exonerated, with no Bifrost in the process

`transcripts/093/sdk-probe.txt` was produced by
`transcripts/093/rig/sdkprobe/main.go` with **no gateway running at all**. It
separates two claims that would otherwise be indistinguishable: whether the SDK
drops a flag it was handed, and whether the SDK's own decoder reads an absent
key as success.

```text
SDK NewToolResultText bytes={"content":[{"type":"text","text":"{\"code\":\"KAIRO_OK\",\"detail\":\"x\"}"}]} key_present=false decoded_is_error=false
SDK NewToolResultError bytes={"content":[{"type":"text","text":"{\"code\":\"KAIRO_FAIL\",\"detail\":\"x\"}"}],"isError":true} key_present=true decoded_is_error=true
SDK HandBuiltIsErrorTrue bytes={"content":[{"type":"text","text":"x"}],"isError":true} key_present=true decoded_is_error=true
DECODE arg1 key_present=true decoded_is_error=true consumer_would_say=FAILURE
...
DECODE arg6 key_present=false decoded_is_error=false consumer_would_say=SUCCESS
```

Three conclusions, each from bytes:

1. `NewToolResultError` **does** put `isError: true` on the wire. The SDK is not
   swallowing the flag.
2. `NewToolResultText` **legitimately** omits it. Omission is the SDK's encoding
   of success, not a bug in the SDK.
3. The SDK's own decoder reads each recorded client result to the verdict a
   consumer would reach, so the drop is in the response and not in the reader.

## Root cause

`transports/bifrost-http/handlers/mcpserver.go:439`, at the pinned tag:

```go
		// Extract content from tool message
		var resultText string
		if toolMessage != nil && toolMessage.Content != nil {
			// Handle ContentStr (string content)
			if toolMessage.Content.ContentStr != nil {
				resultText = *toolMessage.Content.ContentStr
			} else if toolMessage.Content.ContentBlocks != nil {
				// Handle ContentBlocks (structured content)
				for _, block := range toolMessage.Content.ContentBlocks {
					if block.Type == schemas.ChatContentBlockTypeText && block.Text != nil {
						resultText += *block.Text
					}
				}
			}
		}

		// Return result using mcp-go helper
		return mcp.NewToolResultText(resultText), nil
```

Lines 422 to 436 extract **only** `toolMessage.Content`. Line 439 rebuilds the
reply with `mcp.NewToolResultText`, which hardcodes `IsError: false`, so the
`omitempty` tag drops the key. `toolMessage.ChatToolMessage.IsError` is never
read on this path.

The flag is carried correctly everywhere up to that point, which is what pins the
loss to this one line:

- `core/mcp/toolmanager.go:797-798` captures it off the SDK result and hands it
  to the message constructor:
  ```go
	isToolError := toolResponse != nil && toolResponse.IsError
	return createToolResponseMessage(*toolCall, responseText, isToolError), executionConfig.Name, sanitizedToolName, nil
  ```
  The comment four lines above it states the requirement: "toolResponse.IsError
  is the server reporting a failed execution over a successful call, so it must
  reach the model as a failure rather than as ordinary result text."
- `core/schemas/chatcompletions.go:1563` carries it as `IsError *bool`, with a
  comment naming the dialects that map it.

**The file is inconsistent with itself.** Bifrost uses `NewToolResultError` for
errors it detects itself at the same file's `:382`, `:416`, and `:418`. Line 439
is the only tool-result construction on the success path, and it is the one that
cannot express failure. This is not a deliberate transformation: the project
already treats a missing failure marker as a bug elsewhere, in merged PRs
[#5894](https://github.com/maximhq/bifrost/pull/5894) and
[#5450](https://github.com/maximhq/bifrost/pull/5450) (both merged 2026-08-06),
and it regression-tests that in `core/mcp/toolerrormarker_test.go`:

```go
// TestCreateToolResultMessageMarksError covers the agent loop's own failures.
// createToolResultMessage already receives the execution error and stringifies
// it into the content, so leaving IsError unset tells the model that a tool call
// bifrost itself watched fail actually succeeded.
```

Those PRs fixed the **model-facing** path. `mcpserver.go` is Bifrost's own
**MCP-facing** path and was never fixed, which is precisely how the defect
survives in the current release.

## Test

`tool_failure_signal_preserved(caller_result, tool_result)` in
[`crates/harness/src/checks.rs`](../../crates/harness/src/checks.rs). It states
the invariant, not the bug: *for every recorded tool call, the result the caller
was given carries the same failure signal as the result the tool produced.*

Why it is dialect-agnostic and compares key presence rather than value:

- A relay sits between a caller and a tool on both legs, so the interesting
  question is not whether a relay can pass a failure through, but whether it
  passes the **same** failure every time. A relay that forwards a success
  correctly and silently rewrites a failure into a success satisfies every other
  check available here: the call succeeded, the status was fine, the content
  arrived, the response is well formed. Only pairing the caller's view against
  the tool's own view exposes it.
- The failure key is looked up across the spellings a tool-result dialect uses
  (`isError`, `is_error`), because the invariant is about the signal and not
  about one vendor's wire spelling. No vendor is named in the checker.
- **Key presence, not value.** The SDK encodes "did not fail" by omitting the
  key, so an absent key on the caller side and a failing tool side is a real
  loss even though the field would read `false`. Asserting on the value alone
  would pass on the broken bytes. Present-but-`false` against a failed tool is
  also caught, so a future re-encoding of the same defect cannot slip past.
- **Fails closed.** Unreadable, empty, contentless, or non-boolean-flag evidence
  is a `Violation`, never a pass, so a checker cannot satisfy the invariant by
  default when the recording is missing.

Conformance tests in
[`crates/harness/tests/conformance.rs`](../../crates/harness/tests/conformance.rs),
all reading the **recorded raw bytes**, not a summary:

| Test | Asserts |
|---|---|
| `relay_drops_the_failure_signal_of_a_failed_tool_call` | `Violation` on all five violating runs |
| `failure_signal_survives_when_no_relay_is_in_the_path` | `Conformant` on the gateway-absent control |
| `relay_preserves_the_success_signal_of_a_succeeding_tool_call` | `Conformant` on the succeeding-tool control |
| `relay_keeps_the_failure_signal_once_the_flip_is_applied` | `Conformant` on the PR-head differential |
| `bug_093_matrix_agrees_with_the_recorded_bytes` | `results.json` cross-checked against the bytes, per trial |
| `bug_093_consumer_classified_the_failed_call_as_success` | the SDK's own verdict re-derived from the wire |
| `bug_093_sdk_emits_the_failure_key_when_given_one` | the SDK exoneration, read from the probe |
| `bug_093_checker_detects_an_injected_signal_on_recorded_bytes` | the checker is not vacuous on real bytes |

**The violation test asserts `Violation` on purpose.** That is the known-bad
expectation and it is the whole point: the day the gateway stops dropping the
flag, `relay_drops_the_failure_signal_of_a_failed_tool_call` fails and says so.
The last test additionally proves the checker is falsifiable on the recorded
bytes: injecting a flag into a conformant control flips it to `Violation`, and
restoring the flag onto the violating result flips the same pair back to
`Conformant`.

## Bug or not

- **Is the expected behavior really the spec?** Yes, and not from a doc line
  alone. Four independent sources agree. (1) The MCP specification revision
  2026-07-28 server tools "Error Handling" defines `isError: true` in a tool
  result as one of exactly two tool-error mechanisms and tells clients to pass
  tool execution errors to the model so it can self-correct. (2) The receiving
  SDK's own type, `mcp-go` v0.43.2 `mcp/tools.go:47-50`, documents that an unset
  flag is assumed false, i.e. the call was successful. (3) Bifrost's own code
  uses `NewToolResultError` at `:382`, `:416`, and `:418` in the same file, so
  the project has already ruled that a tool failure must be labelled. (4)
  Bifrost's merged PRs #5894 and #5450 plus `core/mcp/toolerrormarker_test.go`
  regression-test the same requirement on the model-facing path. There is no
  reading under which an unlabelled failure is intended.
- **Have maintainers already ruled on it?** Maintainers have **accepted the
  defect**, not the behavior. #7612 is open and the fix PR #7640 is open and
  unmerged. That is a ruling that this is a bug, so this is not a docs defect or
  a feature request. Nothing in the repo classifies the drop as intended.
- **Is the trigger supported usage?** Yes, and it is the plain path. The config
  is one anonymous stdio MCP client, `auth_type: none`, `tools_to_execute: ["*"]`,
  `enforce_auth_on_inference: false`, and the documented `POST /mcp` route. No
  flag is required to reach it and nothing here is operator misuse. The failing
  tool reports failure through the SDK's own `NewToolResultError`, which is the
  documented mechanism.
- **Is a real boundary crossed?** No security boundary; this is a correctness
  boundary. The gateway's contract is that a tool result it returns describes
  what the tool did. It returns a result that misdescribes a failure as a
  success. No auth is involved, so nothing about disclosure is being claimed.
- **What fix would a maintainer ship, in one sentence?** Return
  `mcp.NewToolResultError(resultText)` when `toolMessage.ChatToolMessage.IsError`
  is set and `mcp.NewToolResultText(resultText)` otherwise, which is exactly what
  the open PR #7640 does.
- **Label**: `bug`

## Upstream status

- **Date checked**: 2026-10-01.
- **Checked**: release `transports/v2.2.4` at `ed8371a9779bfbc8aa689d4d77964cf8ce9308bf`,
  `main` at `f790ef25b13385d65f155145ed2729844e51ea07`, and PR head `862b3bea5cd8a940d52fefdfb57a0741efc48c84`.
- **Links**: [maximhq/bifrost#7612](https://github.com/maximhq/bifrost/issues/7612)
  (OPEN, 2026-09-27, 0 comments),
  [maximhq/bifrost#7640](https://github.com/maximhq/bifrost/pull/7640)
  (OPEN, not merged),
  adjacent [#7666](https://github.com/maximhq/bifrost/issues/7666).
- **Classification**: **`duplicate-open`**, independently reproduced on the
  **current** release.

What is new here, given the report is already known upstream:

1. **Byte-level 5 of 5 on the current release.** The reporter used v2.2.3.
   `transports/v2.2.4` is still the current HTTP release and still carries the
   defect, so this is a reproduction on current, not on a superseded version.
2. **The one-line root cause pinned against a named tag**, with the two
   supporting pins that show the flag survives everything up to that line
   (`toolmanager.go:797-798`, `chatcompletions.go:1563`) and the internal
   inconsistency at `:382`/`:416`/`:418`. PR #7640's own description says "I did
   not stand up a stdio MCP server behind a virtual MCP endpoint and run the curl
   from the issue"; the cause is now pinned in source rather than inferred.
3. **SDK versus gateway attribution proven positively**, not assumed. PR #7640's
   tests exercise the function at unit level. The SDK-only probe in
   `transcripts/093/sdk-probe.txt` runs with **no Bifrost process at all** and
   shows the SDK emits the flag when given one, omits it legitimately for a
   success, and reads both recorded shapes to the right verdict. That rules the
   SDK out as a cause on evidence rather than by assumption.
4. **A keyless, gateway-absent control.** The direct stdio leg is the same SDK
   against the same MCP server binary with the gateway removed, so the isolating
   variable is literally the presence of the gateway.
5. **A one-line differential against the pending fix**, on real sockets: 144
   bytes against 159, differing only by `,"isError":true`.
6. **The first MCP protocol-egress invariant this repository covers.** Existing
   MCP findings (074, 087) are about execution counts and lifecycle splicing;
   006/007/059 and siblings are about model-facing field loss. This is the
   first checker on the signal a tool result carries **out** of a gateway.

## Reproduce

Cold start, offline, no credential of any kind. Go 1.26.1 or newer with **CGO
enabled**, because the sqlite config store needs it. Docker is not used: the
pinned tag is compiled from source.

```bash
# The tag under test, and the PR head under differential test.
git clone https://github.com/maximhq/bifrost bifrost-tag
git -C bifrost-tag checkout ed8371a9779bfbc8aa689d4d77964cf8ce9308bf   # transports/v2.2.4

git clone https://github.com/maximhq/bifrost bifrost-pr7640
git -C bifrost-pr7640 checkout 862b3bea5cd8a940d52fefdfb57a0741efc48c84

# Build both gateways and the five rig binaries, then run four cells of five.
mkdir -p bin
python3 transcripts/093/rig/reproduce.py \
  --tag-source "$PWD/bifrost-tag" \
  --pr-source "$PWD/bifrost-pr7640" \
  --rig-dir  "$PWD/transcripts/093/rig" \
  --bin-dir  "$PWD/bin" \
  --build --runs 5
```

**Environment variables: none.** The reproduction needs no provider key, no
`Authorization` header, and no Bifrost auth. Every child process is launched with
a deliberately minimal environment containing only `HOME`, `LANG`, `PATH`, and
`TMPDIR` (`safe_child_env` in the rig), so no caller credential can leak into a
recording even by accident. The rig's only environment variables of its own are
`KAIRO_LEDGER` and `KAIRO_TEE_LOG`, which it sets on the MCP server it spawns to
point at the per-run ledger and byte log. Their values are paths the rig chose.

Two things the script does that a reviewer should know before running it:

- It **raises a build pin in the PR checkout only**. PR #7640 head pins
  `plugins/governance v1.8.3`, whose published module lacks `StartResetWorkers`
  and does not compile, so the script runs `go mod edit -require=...@v1.8.4` and
  `go mod tidy` in `bifrost-pr7640/transports`. `v1.8.4` is the version the
  release tag already pins. This touches `go.mod` and `go.sum` in the PR
  checkout. It touches **no Go file**.
- It **writes a UI placeholder**. The embedded UI is a compile-time `all:ui`
  embed, so the rig creates `transports/bifrost-http/ui/index.html`. The UI is
  not on the `/mcp` request path. Both facts are recorded in `target.json` under
  `runtime_prerequisites` rather than passed over.

The script refuses to proceed if a checkout's `HEAD` is not the expected pinned
commit, and `build_bifrost` refuses to build if `core`,
`transports/bifrost-http/handlers`, `transports/bifrost-http/server`, or
`transports/bifrost-http/lib` has any `.go` modification, so a patched tree
cannot be presented as the pinned one. It counts tool executions in a ledger
written by the MCP server itself, waits for both tools to be served before any
trial is counted, treats a JSON-RPC error body as an error rather than as a
missing key, and exits non-zero if any cell fails or is missing.

To freeze a run in place instead of a temporary directory, add
`--output transcripts/093`, which also runs the rig's own `scan_sanitized` pass
over what it wrote.

## Differential limitation, stated plainly

The two sides of the differential do **not** resolve the same `core` module. The
release tag pins `core v1.11.0`; PR #7640 head pins `core v1.10.4`. That is a
one-version gap that the PR inherited from its base, not a change made here. So
the differential cell is **not** a perfectly single-variable experiment: two
things differ between the two builds, the `mcpserver.go` reply construction and
the `core` module version.

What bounds the risk, stated as measured rather than asserted:

- The probe asserts as a **precondition** that the flag **arrives set in both
  builds**. `results.json` records `upstream_emitted_is_error_true_per_run` as
  true in the violation cell and true in the differential cell, from the recorded
  upstream stdio bytes. So `core`'s capture side, which is the only place a
  `core` difference could plausibly suppress the flag before `mcpserver.go` sees
  it, demonstrably behaves identically in both builds.
- The only **source** file that differs between the two builds' reply paths is
  `mcpserver.go`. `build_bifrost` re-checks after the pin raise that no `.go`
  file under test was modified.
- The violation cell does not depend on the differential at all. It stands on
  the tag alone: upstream bytes show `isError: true` arriving, client bytes show
  it absent, the direct-stdio control shows the same SDK on the same server
  keeping it with the gateway removed. The differential is corroboration, not the
  load-bearing leg.
- The converse leg of the direct control, `control_gateway_success`, shows the
  endpoint **does** emit results through the same constructor on the same build,
  which rules out a protocol-level inability to express the field.

A reviewer who wants a perfectly single-variable differential should rebuild the
tag with `core` forced to v1.10.4, or the PR head with `core` forced to v1.11.0,
and re-run. That was not done here.

## What was not verified

- **The gateway's self-reported version is not discriminating.** All three
  `gateway-version.json` files read `"v1.0.0"`, so `/api/version` does not
  distinguish the tag from the PR head. Identity rests on `git rev-parse HEAD`
  and the binary sha256 recorded in `results.json`, not on the endpoint's own
  string.
- **No real MCP server vendor was tested.** The upstream is the rig's own
  deterministic `mcp-go` server, not a third-party MCP server. The claim is about
  what Bifrost does with a spec-conformant failure result, which the rig's server
  produces through the SDK's own `NewToolResultError`, but a vendor that encodes
  failure some other way was not exercised.
- **Only `mcp-go` v0.43.2 was tested as the client.** A client that reads
  `isError` differently, or that relies on the error text rather than the flag,
  would behave differently. The absence-of-key-reads-as-success consequence rests
  on this SDK's documented type and on the measured decode in `sdk-probe.txt`.
- **The model-facing consequence was not run.** Bifrost's own MCP endpoint is
  for MCP clients, not for model inference, so no agent loop was driven end to
  end and no model was involved. The consumer-boundary measurement is a real MCP
  SDK client reporting SUCCESS, which is one boundary short of a full agent loop.
- **SSE and streamable-HTTP-with-sessions transports were not exercised.** Only
  the unary `POST /mcp` JSON-RPC request/response form was tested. The recorded
  responses are `application/json`, not `text/event-stream`.
- **The `structuredContent` and non-text content losses in
  [#7666](https://github.com/maximhq/bifrost/issues/7666) were not investigated**
  and are not claimed here.
- **Multi-client and concurrency were not tested.** Every run is one client, one
  call, one gateway process.
- **`plugins/governance` was raised to v1.8.4 for the PR build only**, for the
  compile reason above. The tag build used the tag's own pins. If v1.8.4
  introduced a behaviour change on the `/mcp` path it would not have been caught
  here; the SDK precondition probe is the guard against that specific risk.
- **Upstream status was checked on 2026-10-01 only.**
