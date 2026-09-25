# 087, LiteLLM router retries re-execute an MCP tool

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). No dedicated issue or pull request matched on 2026-09-25. [Discussion #36122](https://github.com/BerriAI/litellm/discussions/36122) discusses the general risk that retries can fire side-effecting tools twice, but does not identify or fix LiteLLM's proxy-owned MCP retry boundary.
- **Tool under test**:
  - LiteLLM 1.102.1, the current latest release on 2026-09-25 ([tag v1.102.1](https://github.com/BerriAI/litellm/releases/tag/v1.102.1)).
  - LiteLLM main at [`2701e200`](https://github.com/BerriAI/litellm/commit/2701e2008baee4ed2c16cba7a56ea7f96d6d0981), which reports 1.104.0.
  - OpenAI Python SDK 2.54.0, MCP SDK 1.30.0 on the release and 2.2.0 on main, Python 3.12.13.
- **Reproduced**: 2026-09-25, locally, through the real LiteLLM proxy CLI and `POST /v1/chat/completions`.

## What breaks

An application sends one Chat Completions request through LiteLLM with a gateway-hosted MCP tool and `require_approval: "never"`. The model asks for that tool once. LiteLLM executes it, sends the tool result to the model, and receives a retryable HTTP 500 on the follow-up model call.

The router retries the whole public request. Each retry starts the MCP agent loop again, obtains the same tool call, and executes the side effect again. With the default two router retries, one client request executes the tool three times and still returns an error.

```text
Client -> LiteLLM Router -> initial model call -> MCP write #1 -> follow-up 500
                          -> retry initial call -> MCP write #2 -> follow-up 500
                          -> retry initial call -> MCP write #3 -> follow-up 500
Client <- HTTP 500
```

This hurts operators that use proxy-owned MCP tools for actions such as sending a notification, creating a ticket, changing a record, or charging an account. The measured harness appends a non-idempotent ledger entry. The production examples are inferred consequences, not live actions performed by this test.

The trigger is deterministic:

| Cell | Fault | Router retries | SDK retries | Tool executions | Upstream calls | Result |
|---|---|---:|---:|---:|---:|---|
| `violation_default_retries` | follow-up 500 | default, 2 | 0 | 3 in 5/5 | 6 in 5/5 | HTTP 500 |
| `violation_num_retries_2` | follow-up 500 | 2 | 0 | 3 in 5/5 | 6 in 5/5 | HTTP 500 |
| `control_fault_off` | off | 2 | 0 | 1 in 5/5 | 2 in 5/5 | HTTP 200 |
| `control_num_retries_0` | follow-up 500 | 0 | 0 | 1 in 5/5 | 2 in 5/5 | HTTP 500 |

The same matrix reproduced on 1.102.1 and main `2701e200`.

## Wire evidence

The release evidence is under [`transcripts/087/cells/`](../../transcripts/087/cells/), with its N-of-N summary in [`results.json`](../../transcripts/087/results.json). Current-main evidence is under [`transcripts/087/matrix/main-2701e200/`](../../transcripts/087/matrix/main-2701e200/).

Every trial retains:

- `client-request.http`: the sanitized raw SDK request to the public proxy route.
- `client-response.http`: the sanitized raw proxy response.
- `upstream/*.http`: every exact request LiteLLM forwarded and every deterministic upstream response.
- `upstream/*.json`: ordered metadata that distinguishes the initial model call from the follow-up fault.
- `tool-ledger.jsonl`: one record written at the MCP consumer boundary per actual tool execution.
- `result.json`: counts derived from those raw artifacts.

For example, `violation_num_retries_2/run-01` contains six upstream exchanges in the order `initial, followup-fault` repeated three times. Its ledger is:

```jsonl
{"seq":1,"entry":"alpha"}
{"seq":2,"entry":"alpha"}
{"seq":3,"entry":"alpha"}
```

Both controls contain one ledger line. The SDK has `max_retries=0`, so the extra executions cannot come from the client. The artifacts contain only synthetic prompts and inline-marked synthetic-key redactions.

## Root cause (if found)

The non-streaming MCP wrapper documents that `require_approval="never"` causes it to execute the tool and then make a follow-up model call ([current main, handler lines 85-103](https://github.com/BerriAI/litellm/blob/2701e2008baee4ed2c16cba7a56ea7f96d6d0981/litellm/responses/mcp/chat_completions_handler.py#L85-L103)). It calls the MCP tool at [lines 608-621](https://github.com/BerriAI/litellm/blob/2701e2008baee4ed2c16cba7a56ea7f96d6d0981/litellm/responses/mcp/chat_completions_handler.py#L608-L621), then makes the fallible follow-up model call at [lines 631-643](https://github.com/BerriAI/litellm/blob/2701e2008baee4ed2c16cba7a56ea7f96d6d0981/litellm/responses/mcp/chat_completions_handler.py#L631-L643).

The router wraps its original request function in `async_function_with_retries` ([lines 7441-7490](https://github.com/BerriAI/litellm/blob/2701e2008baee4ed2c16cba7a56ea7f96d6d0981/litellm/router.py#L7441-L7490)). After the follow-up 500 escapes, the retry loop calls the same original function again ([lines 7576-7697](https://github.com/BerriAI/litellm/blob/2701e2008baee4ed2c16cba7a56ea7f96d6d0981/litellm/router.py#L7576-L7697)). The retry boundary therefore encloses discovery, the initial model call, MCP execution, and the follow-up model call instead of retrying only the failed model hop.

The same structure exists in 1.102.1. Its MCP execution is at `chat_completions_handler.py:605` and its follow-up call is at `:635`; `router.py:7692` retries the enclosing original function.

## Gate 1: correctness

### Exact reproduction

From a clean kairo checkout on Python 3.12:

```text
uv venv .venv-087-pinned --python 3.12
uv pip install --python .venv-087-pinned/bin/python 'litellm[proxy]==1.102.1' 'mcp==1.30.0' 'openai==2.54.0'
.venv-087-pinned/bin/python transcripts/087/reproduce.py \
  --python .venv-087-pinned/bin/python \
  --expect-litellm 1.102.1 \
  --source-ref v1.102.1 \
  --captured-at 2026-09-25 \
  --output-dir reviewer-output/1.102.1
python3 transcripts/087/check_evidence.py reviewer-output/1.102.1/results.json
```

For current main:

```text
git clone --depth 1 https://github.com/BerriAI/litellm.git ../litellm-087-main
uv venv .venv-087-main --python 3.12
uv pip install --python .venv-087-main/bin/python --editable '../litellm-087-main[proxy]'
.venv-087-main/bin/python transcripts/087/reproduce.py \
  --python .venv-087-main/bin/python \
  --expect-litellm 1.104.0 \
  --source-ref 2701e2008baee4ed2c16cba7a56ea7f96d6d0981 \
  --captured-at 2026-09-25 \
  --output-dir reviewer-output/main-2701e200
python3 transcripts/087/check_evidence.py reviewer-output/main-2701e200/results.json
```

- **Expected behavior**: one public client request executes one side-effecting MCP call at most once, even if a later model hop fails and the router retries that hop.
- **Observed behavior**: both retry-enabled cells execute the tool three times. The same request executes it once when retries are disabled.
- **Raw request evidence**: each run's `client-request.http` records one public request with `type: "mcp"`, `server_url: "litellm_proxy"`, `require_approval: "never"`, and `tool_choice: "required"`.
- **Raw response evidence**: each violation returns one HTTP 500 after three executions. The fault-off control returns HTTP 200 after one execution.
- **Forwarded-request evidence**: each violation records three initial model calls and three failed follow-up calls. Both controls record one initial call and one follow-up call.
- **Reproduction rate**: 5/5 per cell on each target, 40 total client requests.
- **Smallest isolated trigger**: a retryable failure after LiteLLM has executed an auto-approved MCP tool, with router retries greater than zero.

### Hosted closed-source evidence

N/A. LiteLLM is open source and both targets were run locally.

### Control

`control_num_retries_0` changes only `litellm_settings.num_retries` from 2 to 0. It keeps the route, client, request body, model behavior, MCP server, and injected follow-up 500 fixed. The tool executes once in 5/5 trials and the client receives the same error class.

`control_fault_off` changes only the follow-up upstream response from HTTP 500 to HTTP 200. With retries still set to 2, the tool executes once and the request succeeds in 5/5 trials.

Together they rule out the SDK, the initial model response, tool discovery, MCP transport, and malformed input. The repeated action follows LiteLLM's router retry boundary.

### False-positive checks

- [x] Tested the exact MCP retry behavior, not a nearby streaming symptom.
- [x] Pinned release 1.102.1 and current main commit `2701e200`.
- [x] Ruled out bad configuration and malformed input with the successful fault-off control.
- [x] Ruled out model nondeterminism with a deterministic upstream that emits one fixed tool call per attempt.
- [x] Confirmed the failure is not created only by summary logic: raw forwarded HTTP and the independently written MCP ledger agree in every trial.
- [x] Sanitized all recorded evidence.

## Gate 2: usefulness

### Who gets bitten

- **Affected user or customer**: an AI Gateway operator who lets LiteLLM auto-execute an MCP tool with a side effect.
- **Real workflow**: one agent request calls a write tool, then the provider transiently fails while generating the post-tool answer.
- **Preconditions and likely frequency**: `server_url: "litellm_proxy"`, `require_approval: "never"`, a tool call, a retryable failure after execution, and router retries above zero. The consequence occurred in 10/10 faulting retry-enabled trials per target. The production frequency of post-tool retryable failures was not measured.

### Observable consequence

- **User action**: submit one request asking the agent to perform one write.
- **Wire-level defect**: the proxy sends three initial model requests and three follow-up requests because its outer retry repeats the complete MCP loop.
- **End-user or agent-level failure**: the MCP server receives and performs three writes, while the client sees only one final HTTP 500.
- **Consumer-boundary demonstration or transcript**: every violation `tool-ledger.jsonl` has three entries; both controls have one.
- **Measured impact**: one synthetic request produces three non-idempotent ledger writes, 5/5 on both versions and both retry-enabled configurations.
- **Inferred impact**: a production tool without its own idempotency protection can create duplicate tickets, notifications, updates, or charges.

### Bug or not

- **Expected behavior confirmed as intended spec, not a stale doc line**: the current README documents `POST /v1/chat/completions` with `server_url: "litellm_proxy"` and `require_approval: "never"`. The handler says this mode executes the returned tool and makes a follow-up call. The current integration test is named `test_auto_approved_gateway_tool_is_listed_executed_once_and_fed_back` and asserts one peer call across Chat, Responses, and Messages ([lines 261-273](https://github.com/BerriAI/litellm/blob/2701e2008baee4ed2c16cba7a56ea7f96d6d0981/tests/integration/mcp/test_mcp_llm_endpoints.py#L261-L273)). The dashboard Agent Builder emits `require_approval: "never"` by default for an MCP reference ([source](https://github.com/BerriAI/litellm/blob/2701e2008baee4ed2c16cba7a56ea7f96d6d0981/ui/litellm-dashboard/src/app/%28dashboard%29/playground/components/chat_ui/AgentBuilderView.tsx#L164-L174)).
- **Maintainer ruling searched and result**: no issue, pull request, commit, test, or code comment says gateway retries may repeat a completed MCP side effect. Discussion #36122 notes the general duplicate-tool risk and proposes downstream idempotency, but has no maintainer ruling and does not cover this proxy-owned execution path.
- **Trigger is supported usage with default or recommended settings**: the repository README and dashboard use the exact MCP reference shape. The violation also reproduces with the router's default two retries.
- **Boundary crossed**: yes. This is not a disclosure claim. One authorized client action crosses the execution boundary three times, and the three writes are observed at the MCP server rather than inferred from model output.
- **Fix a maintainer would ship, in one sentence**: once the MCP handler has executed a tool, do not let an outer router retry replay the full MCP loop; retry only the failed follow-up model call or surface the error.
- **Label**: `bug`.

## Gate 3: upstream status

- **Date checked**: 2026-09-25.
- **Upstream release and commit checked**: latest release 1.102.1 and main `2701e200`.
- **Search terms used**: `MCP retry`, `MCP duplicate`, `MCP retry execute tool twice`, `MCP re-execute retry`, `require_approval never retry`, `num_retries MCP`, `side effect retry tool`, `tool calls retries`, `MCP retry tool execution`.
- **Issues searched**: open and closed GitHub issues, including adjacent [#31910](https://github.com/BerriAI/litellm/issues/31910), [#32562](https://github.com/BerriAI/litellm/issues/32562), [#37031](https://github.com/BerriAI/litellm/issues/37031), and fixed [#40118](https://github.com/BerriAI/litellm/issues/40118). None covers router retries re-executing a completed MCP call.
- **Pull requests searched**: open, closed, and merged pull requests with the same terms. The current MCP and router changes do not move this retry boundary or add idempotency.
- **Releases, changelog, and documentation searched**: release list through 1.102.1, the current MCP documentation, README MCP example, routing documentation, and current changelog entries.
- **Relevant commits searched**: current main and GitHub commit search for MCP, retry, duplicate tool, re-execute, `require_approval`, and `num_retries` terms.
- **Matching links**: [Discussion #36122](https://github.com/BerriAI/litellm/discussions/36122) discusses the generic risk without a ticket or gateway fix. [Issue #37031](https://github.com/BerriAI/litellm/issues/37031) is a different MCP auto-execution bug. [Issue #40118](https://github.com/BerriAI/litellm/issues/40118) is a fixed Responses streaming lifecycle bug.

Classification: `discussed-no-ticket`.

What is new here is a deterministic reproduction of LiteLLM's own MCP gateway executing the side effect three times, raw evidence that isolates the outer router retry, current-main confirmation, and an invariant checker tied to the consumer ledger.

## Frozen invariant

- **Issue writeup**: this file.
- **Checker added**: `mcp_tool_executes_once` in `crates/harness/src/checks.rs`, plus `transcripts/087/check_evidence.py` for cross-artifact reconciliation.
- **Conformance test added**: `litellm_mcp_router_retries_reexecute_the_tool` and `issue_087_checker_rejects_vacuous_malformed_and_inconsistent_evidence` in `crates/harness/tests/conformance.rs`.
- **Why this is an invariant**: the checker reads the MCP consumer's execution ledger and asks whether one client turn executed the tool once. It does not depend on LiteLLM function names, retry headers, or a hard-coded response body.

## Test

| Check | Command | Result |
|---|---|---|
| Pinned reproduction | `transcripts/087/reproduce.py` on 1.102.1 | pass, four cells 5/5 |
| Current-main reproduction | `transcripts/087/reproduce.py` on `2701e200` | pass, four cells 5/5 |
| Evidence checker | `python3 transcripts/087/check_evidence.py .../results.json` | pass on both matrices |
| Python mutation tests | `python3 -m unittest transcripts/087/test_reproduce.py` | pass, 5 tests |
| Focused conformance | `cargo test -p kairo issue_087` and `cargo test -p kairo litellm_mcp_router` | pass |
| Harness | `cargo test --workspace` | pass: 40 unit and 171 conformance tests |
| Formatting | `cargo fmt --all -- --check` | pass |
| Lint | `cargo clippy --workspace --all-targets --all-features -- -D warnings` | pass |
| README counts | `python3 tools/update-readme-counts.py --check` | pass: 63 findings and 211 tests |

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized response is committed.
- [x] Only environment variable names appear in commands and documentation.
- [x] The pull request contains one finding.
- [x] Unrelated generated files and local state are excluded.

## Author verdict

- **Correctness**: PASS.
- **Usefulness**: PASS.
- **Upstream status**: PASS.
- **Overall**: NEEDS EVIDENCE until the required independent read-only reviewer reruns the critical path and passes all three gates. This author report does not claim `ACCEPT`.

## Independent review

Run `.github/agents/kairo-reproduction-reviewer.agent.md` against this pull request. Approval is blocked until that reviewer independently reruns the critical path and all three gates pass.
