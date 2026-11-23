# 148, Bifrost virtual-key provider file ownership check skips tool-output messages, allowing cross-tenant file access via `function_call_output`, `computer_call_output`, and Anthropic `tool_result`

- **Upstream**: [maximhq/bifrost#5025](https://github.com/maximhq/bifrost/issues/5025) (open), partially remediated by [#8292](https://github.com/maximhq/bifrost/pull/8292), [#8317](https://github.com/maximhq/bifrost/pull/8317), and [#8427](https://github.com/maximhq/bifrost/pull/8427)
- **Tool under test**: Bifrost `dev` at `783c682951a1c4d6a07a677779e0becab8bbbe2c` (core `1.16.0`, binary `1.16.0-783c682951`; `plugins/governance/providerjobs.go` is byte-identical in release `transports/v2.6.0` at commit `172617bb045c6f43d697843c115e64f0155ed143`, tag `d10f541617acbda020ddaffce31f8156c60d62d6`)
- **Reproduced**: 2026-11-23, local (`POST /v1/files`, `GET /v1/files/{file_id}`, `POST /v1/responses`, `POST /anthropic/v1/messages`, `client.enforce_auth_on_inference=true`, `client.enforce_vk_object_ownership=true`)

## Claim

When virtual keys share a provider credential and object ownership isolation is enabled (`client.enforce_vk_object_ownership=true`, the default), Bifrost records the uploading virtual key for each provider file created via `POST /v1/files` and blocks other virtual keys from retrieving that file (`GET /v1/files/{id}`) or referencing its `file_id` inside user messages on `POST /v1/responses` and `POST /anthropic/v1/messages`. However, `inferenceFileIDs` (`plugins/governance/providerjobs.go:689-704`) skips every `ResponsesMessage` where `message.Content == nil` without inspecting `message.ResponsesToolMessage`. Because tool-output items (`function_call_output`, `custom_tool_call_output`, `computer_call_output`, and Anthropic `tool_result` blocks converted by `ToBifrostResponsesRequest`) populate `ResponsesToolMessage` with `Content == nil`, any virtual key can reference another virtual key's private `file_id` inside a tool output and Bifrost forwards it upstream under the shared provider credential.

- Upstream project: [maximhq/bifrost](https://github.com/maximhq/bifrost)
- Cited upstream issue or report: [#5025](https://github.com/maximhq/bifrost/issues/5025) (open), partially remediated by [#8292](https://github.com/maximhq/bifrost/pull/8292), [#8317](https://github.com/maximhq/bifrost/pull/8317), and [#8427](https://github.com/maximhq/bifrost/pull/8427)
- Target type: open-source/local
- Tested release or commit, or hosted service fingerprint and UTC window: `dev` at `783c682951a1c4d6a07a677779e0becab8bbbe2c`, core `1.16.0`, binary version `1.16.0-783c682951` (`plugins/governance/providerjobs.go` is byte-identical in `transports/v2.6.0` at commit `172617bb045c6f43d697843c115e64f0155ed143`)
- Client dialect and endpoint: OpenAI Responses (`POST /v1/responses`) and Anthropic Messages (`POST /anthropic/v1/messages`), after file upload via OpenAI Files (`POST /v1/files?provider=openai`)
- Backend dialect and provider or capture upstream: OpenAI Responses (`POST /v1/responses`) and OpenAI Files (`POST /v1/files`), deterministic local capture upstream
- Model, if relevant: `openai/gpt-4o-mini`; model generation is irrelevant to the pre-call governance ownership check
- Relevant configuration: `client.enforce_auth_on_inference=true`, `client.enforce_vk_object_ownership=true` (default), `config_store.enabled=true` (SQLite), two virtual keys (`vk-tenant-a`, `vk-tenant-b`) sharing one OpenAI provider credential (`key-openai-1`)

## What breaks

Operators running Bifrost as a multi-tenant gateway assign distinct virtual keys (`vk-tenant-a`, `vk-tenant-b`) to different tenants or teams that share an upstream OpenAI provider key. Bifrost's `enforce_vk_object_ownership` feature (enabled by default in `DefaultClientConfig` at `transports/bifrost-http/lib/config.go:882`) records `(provider, object_type="file", object_id, virtual_key_id)` in `governance_provider_objects` whenever a virtual key uploads a file, and rejects cross-virtual-key access to that `file_id` on direct file endpoints (`GET /v1/files/{id}`) and inference endpoints (`POST /v1/chat/completions`, `POST /v1/responses`, `POST /anthropic/v1/messages`) with HTTP 404 (`file '<file_id>' not found`).

Because `inferenceFileIDs` in `plugins/governance/providerjobs.go:689-704` only inspects `message.Content.ContentBlocks` and executes `if message.Content == nil { continue }`, it ignores `message.ResponsesToolMessage`. In Bifrost's schema (`core/schemas/responses.go:1570-1612`, `2208-2261`, `2608-2680`, and `2816-2825`), tool-output messages (`function_call_output`, `custom_tool_call_output`, and `computer_call_output`) embed `ResponsesToolMessage` and leave `Content == nil`. Furthermore, on `POST /anthropic/v1/messages`, `ToBifrostResponsesRequest` (`core/providers/anthropic/responses.go:7321-7407`) translates every Anthropic `tool_result` containing a `document` or `image` file reference into a `ResponsesMessageTypeFunctionCallOutput` message with `Content == nil` and `ResponsesToolMessage.Output.ResponsesFunctionToolCallOutputBlocks` holding the `input_file` block. As a result, Tenant B can embed Tenant A's `file_id` in any tool output on `/v1/responses` or `/anthropic/v1/messages`, bypass the virtual-key ownership check completely, and cause Bifrost to forward Tenant A's file reference to the upstream provider under the shared provider API key.

## Wire evidence

- [`tenant-a-file-upload.http`](../../transcripts/148/tenant-a-file-upload.http), Tenant A uploads `tenant-a-secret.pdf` via `POST /v1/files?provider=openai` and Bifrost records ownership of `file-tenant-a-secret-148` under `vk-tenant-a`
- [`control-direct-file-retrieve.http`](../../transcripts/148/control-direct-file-retrieve.http), Tenant B requests `GET /v1/files/file-tenant-a-secret-148?provider=openai` and Bifrost rejects it with HTTP 404 (`access_blocked`) before any upstream call
- [`control-responses-user-message.http`](../../transcripts/148/control-responses-user-message.http), Tenant B references `file-tenant-a-secret-148` in a `role: "user"` message on `POST /v1/responses` and Bifrost rejects it with HTTP 404 (`access_blocked`) before any upstream call
- [`control-anthropic-user-message.http`](../../transcripts/148/control-anthropic-user-message.http), Tenant B references `file-tenant-a-secret-148` in a `role: "user"` `document` block on `POST /anthropic/v1/messages` and Bifrost rejects it with HTTP 404 (`not_found_error`) before any upstream call
- [`control-owner-tool-output.http`](../../transcripts/148/control-owner-tool-output.http), Tenant A (the owner) references `file-tenant-a-secret-148` in a `function_call_output` block on `POST /v1/responses` and Bifrost serves it with HTTP 200
- [`attack-responses-function-call-output.http`](../../transcripts/148/attack-responses-function-call-output.http) and [`attack-responses-function-call-output-upstream.http`](../../transcripts/148/attack-responses-function-call-output-upstream.http), Tenant B references `file-tenant-a-secret-148` inside `function_call_output.output` on `POST /v1/responses`; Bifrost returns HTTP 200 and forwards `file-tenant-a-secret-148` upstream under the shared provider credential
- [`attack-responses-computer-call-output.http`](../../transcripts/148/attack-responses-computer-call-output.http) and [`attack-responses-computer-call-output-upstream.http`](../../transcripts/148/attack-responses-computer-call-output-upstream.http), Tenant B references `file-tenant-a-secret-148` inside `computer_call_output.output` on `POST /v1/responses`; Bifrost returns HTTP 200 and forwards `file-tenant-a-secret-148` upstream
- [`attack-anthropic-tool-result.http`](../../transcripts/148/attack-anthropic-tool-result.http) and [`attack-anthropic-tool-result-upstream.http`](../../transcripts/148/attack-anthropic-tool-result-upstream.http), Tenant B references `file-tenant-a-secret-148` inside a `tool_result` `document` block on `POST /anthropic/v1/messages`; Bifrost returns HTTP 200 and forwards `file-tenant-a-secret-148` upstream

## Root cause

In [`plugins/governance/providerjobs.go:659-706`](https://github.com/maximhq/bifrost/blob/783c682951a1c4d6a07a677779e0becab8bbbe2c/plugins/governance/providerjobs.go#L659-L706), `inferenceFileIDs` extracts provider file references from chat and responses requests for the pre-call ownership check (`enforceProviderObjectOwnership` at lines 1056-1155):

```go
	case req.ResponsesRequest != nil:
		for _, message := range req.ResponsesRequest.Input {
			if message.Content == nil {
				continue
			}
			for _, block := range message.Content.ContentBlocks {
				add(block.FileID)
				if block.ResponsesInputMessageContentBlockFile != nil {
					add(geminiFileName(block.ResponsesInputMessageContentBlockFile.FileURL))
				}
				if block.ResponsesInputMessageContentBlockImage != nil {
					add(geminiFileName(block.ResponsesInputMessageContentBlockImage.ImageURL))
				}
			}
		}
	}
```

In [`core/schemas/responses.go:1570-1612`](https://github.com/maximhq/bifrost/blob/783c682951a1c4d6a07a677779e0becab8bbbe2c/core/schemas/responses.go#L1570-L1612), [`2208-2261`](https://github.com/maximhq/bifrost/blob/783c682951a1c4d6a07a677779e0becab8bbbe2c/core/schemas/responses.go#L2208-L2261), [`2608-2680`](https://github.com/maximhq/bifrost/blob/783c682951a1c4d6a07a677779e0becab8bbbe2c/core/schemas/responses.go#L2608-L2680), and [`2816-2825`](https://github.com/maximhq/bifrost/blob/783c682951a1c4d6a07a677779e0becab8bbbe2c/core/schemas/responses.go#L2816-L2825), a `ResponsesMessage` of type `function_call_output`, `custom_tool_call_output`, or `computer_call_output` unmarshals its `"output"` field into `message.ResponsesToolMessage.Output` (`ResponsesFunctionToolCallOutputBlocks` or `ResponsesComputerToolCallOutput.FileID`), leaving `message.Content == nil`. Likewise, on the Anthropic Messages ingress path, [`core/providers/anthropic/responses.go:7321-7407`](https://github.com/maximhq/bifrost/blob/783c682951a1c4d6a07a677779e0becab8bbbe2c/core/providers/anthropic/responses.go#L7321-L7407) converts `tool_result` blocks containing `document` or `image` file references into `ResponsesMessageTypeFunctionCallOutput` items with `Content: nil` and `ResponsesToolMessage.Output.ResponsesFunctionToolCallOutputBlocks`. Because `inferenceFileIDs` skips every message with `message.Content == nil`, `providerObjectReferences` returns `nil` and `enforceProviderObjectOwnership` permits the request.

## Gate 1: correctness

### Exact reproduction

```text
git clone https://github.com/maximhq/bifrost.git /tmp/bifrost-148
git -C /tmp/bifrost-148 checkout 783c682951a1c4d6a07a677779e0becab8bbbe2c
(cd /tmp/bifrost-148 && \
  rm -f go.work go.work.sum && \
  go work init ./core ./framework ./transports ./plugins/* && \
  go work sync && \
  git checkout -- . && \
  mkdir -p transports/bifrost-http/ui && \
  touch transports/bifrost-http/ui/index.html && \
  cd transports/bifrost-http && \
  go build -ldflags='-X main.Version=1.16.0-783c682951' \
    -o /tmp/bifrost-148-http .)

BIFROST_SOURCE=/tmp/bifrost-148 \
BIFROST_BIN=/tmp/bifrost-148-http \
python3 transcripts/148/reproduce.py
```

- Expected behavior: With `client.enforce_vk_object_ownership=true`, Tenant B (`vk-tenant-b`) must be rejected with HTTP 404 (`access_blocked` / `not_found_error`) whenever it references Tenant A's uploaded file (`file-tenant-a-secret-148`), regardless of whether the `file_id` appears in a top-level user message or a tool-output block, and no request carrying `file-tenant-a-secret-148` may be forwarded upstream on Tenant B's behalf.
- Observed behavior: Bifrost rejects Tenant B with HTTP 404 on `GET /v1/files/file-tenant-a-secret-148?provider=openai` (5/5) and on user-message file references in `POST /v1/responses` (5/5) and `POST /anthropic/v1/messages` (5/5), but returns HTTP 200 and forwards Tenant A's `file-tenant-a-secret-148` upstream under the shared provider credential when Tenant B places the exact same `file_id` in `function_call_output` (5/5), `computer_call_output` (5/5), or Anthropic `tool_result` (5/5).
- Raw request evidence: [`attack-responses-function-call-output.http`](../../transcripts/148/attack-responses-function-call-output.http), [`attack-responses-computer-call-output.http`](../../transcripts/148/attack-responses-computer-call-output.http), [`attack-anthropic-tool-result.http`](../../transcripts/148/attack-anthropic-tool-result.http)
- Raw response evidence: [`attack-responses-function-call-output.http`](../../transcripts/148/attack-responses-function-call-output.http), [`attack-responses-computer-call-output.http`](../../transcripts/148/attack-responses-computer-call-output.http), [`attack-anthropic-tool-result.http`](../../transcripts/148/attack-anthropic-tool-result.http)
- Forwarded-request evidence, if applicable: [`attack-responses-function-call-output-upstream.http`](../../transcripts/148/attack-responses-function-call-output-upstream.http), [`attack-responses-computer-call-output-upstream.http`](../../transcripts/148/attack-responses-computer-call-output-upstream.http), [`attack-anthropic-tool-result-upstream.http`](../../transcripts/148/attack-anthropic-tool-result-upstream.http)
- Reproduction rate: 5/5 trials across all 7 conditions (35 trial requests plus initial file upload).
- Smallest isolated trigger: Moving a foreign virtual key's `file_id` from `input[].content[]` (where `message.Content != nil`) into `input[].output[]` on a `function_call_output` item (where `message.Content == nil` and `message.ResponsesToolMessage != nil`).

### Control

```text
# The same runner executes four controls alongside the three attack vectors in every trial:
# 1. Tenant B direct GET /v1/files/file-tenant-a-secret-148?provider=openai -> HTTP 404 (0 upstream calls)
# 2. Tenant B POST /v1/responses with file-tenant-a-secret-148 in user message content -> HTTP 404 (0 upstream calls)
# 3. Tenant B POST /anthropic/v1/messages with file-tenant-a-secret-148 in user message document -> HTTP 404 (0 upstream calls)
# 4. Owner Tenant A POST /v1/responses with file-tenant-a-secret-148 in function_call_output -> HTTP 200 (1 upstream call)
BIFROST_SOURCE=/tmp/bifrost-148 \
BIFROST_BIN=/tmp/bifrost-148-http \
python3 transcripts/148/reproduce.py
```

- Control result: All three cross-VK controls reject Tenant B with HTTP 404 and zero forwarded upstream requests (5/5 each), and the owner positive control allows Tenant A to use its own `file_id` inside `function_call_output` with HTTP 200 (5/5).
- Why this attributes the failure to the claimed layer: Controls 2 and 3 prove that Bifrost's governance plugin (`enforceProviderObjectOwnership`) is active, knows `file-tenant-a-secret-148` belongs to `vk-tenant-a`, and blocks `vk-tenant-b` on the exact same `/v1/responses` and `/anthropic/v1/messages` routes when `file_id` sits in `message.Content`. Changing only the message item from a user message to a tool output (`function_call_output`, `computer_call_output`, or `tool_result`) causes `inferenceFileIDs` to return an empty slice and bypasses the check.

### False-positive checks

- [x] Tested the exact cited behavior, not a similar symptom.
- [x] Pinned and reported the target version or commit.
- [x] Ruled out bad configuration and malformed input.
- [x] Ruled out model nondeterminism or reported why it is irrelevant.
- [x] Confirmed the failure is not created only by the mock or harness.
- [x] Sanitized all recorded evidence.

## Gate 2: usefulness

### Who gets bitten

- Affected user or customer: Any multi-tenant Bifrost operator or enterprise deployment issuing distinct virtual keys to separate teams, customers, or applications that share an upstream provider credential (OpenAI, Anthropic, Azure, or Bedrock) and rely on Bifrost's default `enforce_vk_object_ownership=true` to isolate uploaded files across virtual keys.
- Real workflow: Tenant A uploads a sensitive document via `POST /v1/files` for document analysis or RAG. Tenant B, holding a separate virtual key on the same Bifrost gateway, submits a normal multi-turn tool-use request to `POST /v1/responses` or `POST /anthropic/v1/messages` referencing Tenant A's `file_id` inside a tool result (`function_call_output`, `computer_call_output`, or `tool_result`) and asks the model to extract or summarize its contents.
- Preconditions and likely frequency: Default Bifrost configuration (`enforce_vk_object_ownership` defaults to `true`), two virtual keys sharing a provider key, and the attacker knowing or guessing the target `file_id` (or learning it from logs or shared identifiers). Under these preconditions, the bypass succeeds deterministically (5/5).

### Observable consequence

- User action: Tenant B sends `POST /v1/responses` or `POST /anthropic/v1/messages` with its own virtual key (`x-bf-vk`) and places Tenant A's `file_id` inside a tool output item.
- Wire-level defect: Bifrost fails to reject the request at `PreLLMHook` (`enforceProviderObjectOwnership`) and forwards Tenant A's `file_id` to the upstream provider authenticated with the shared provider API key.
- End-user or agent-level failure: The upstream provider resolves Tenant A's private file (because the file was uploaded under the same shared provider account) and feeds Tenant A's private document or image content into Tenant B's model response.
- Consumer-boundary demonstration or transcript: [`attack-responses-function-call-output.http`](../../transcripts/148/attack-responses-function-call-output.http), [`attack-responses-computer-call-output.http`](../../transcripts/148/attack-responses-computer-call-output.http), and [`attack-anthropic-tool-result.http`](../../transcripts/148/attack-anthropic-tool-result.http) along with their `*-upstream.http` captures demonstrate Tenant B receiving HTTP 200 with the upstream response after Bifrost forwards Tenant A's `file-tenant-a-secret-148`.
- Measured impact: 5/5 bypass of virtual-key file ownership enforcement across all three tool-output surfaces (`function_call_output`, `computer_call_output`, and Anthropic `tool_result`), whereas user-message references to the same `file_id` on the same routes are blocked 5/5 with HTTP 404.
- Inferred impact, clearly labeled: Against a live OpenAI or Anthropic account shared by multiple virtual keys, the upstream provider reads the referenced file from the shared provider workspace and returns answers derived from Tenant A's confidential file to Tenant B.

### Bug or not

- Expected behavior confirmed as intended spec, not a stale doc line (cite examples, tests, or UI): Yes. Upstream issue [#5025](https://github.com/maximhq/bifrost/issues/5025) and PRs [#8292](https://github.com/maximhq/bifrost/pull/8292), [#8317](https://github.com/maximhq/bifrost/pull/8317), and [#8427](https://github.com/maximhq/bifrost/pull/8427) explicitly designed `inferenceFileIDs` and `enforceProviderObjectOwnership` so that a virtual key cannot reference a file uploaded by another virtual key in inference requests, tested in [`plugins/governance/providerjobs_test.go:935-953`](https://github.com/maximhq/bifrost/blob/783c682951a1c4d6a07a677779e0becab8bbbe2c/plugins/governance/providerjobs_test.go#L935-L953).
- Maintainer ruling searched (commit, PR, comment, denylist) and result: Maintainers explicitly ruled in [#8292](https://github.com/maximhq/bifrost/pull/8292) (`fix(governance): isolate batches, files, fine-tuning jobs and containers per virtual key`) and [#8317](https://github.com/maximhq/bifrost/pull/8317) (`fix: close VK object ownership follow-up gaps`) that every inference path carrying a provider `file_id` must enforce virtual-key ownership. Tool-output blocks in `ResponsesToolMessage` were simply overlooked when writing `inferenceFileIDs`.
- Trigger is supported usage with default or recommended settings: Yes. `client.enforce_vk_object_ownership` defaults to `true` (`DefaultClientConfig` at `transports/bifrost-http/lib/config.go:882`), and `input_file` / `input_image` / `computer_screenshot` inside tool outputs as well as `document` / `image` inside Anthropic `tool_result` blocks are first-class supported schemas in `core/schemas/responses.go:2208-2261` and `core/providers/anthropic/responses.go:7321-7407`.
- Boundary crossed (role or key scope that must not see the data, with auth enabled): Tenant B's virtual key (`vk-tenant-b`) crosses the tenant object-ownership boundary to use a provider file owned exclusively by Tenant A's virtual key (`vk-tenant-a`) with `enforce_auth_on_inference=true` and `enforce_vk_object_ownership=true`.
- Fix a maintainer would ship, in one sentence: Update `inferenceFileIDs` in `plugins/governance/providerjobs.go` to also inspect `message.ResponsesToolMessage.Output.ResponsesFunctionToolCallOutputBlocks` (for file and image `FileID`s and Gemini file URIs) and `message.ResponsesToolMessage.Output.ResponsesComputerToolCallOutput.FileID` when scanning `req.ResponsesRequest.Input`.
- Label: `bug`

## Gate 3: upstream status

- Date checked: 2026-11-23
- Upstream version checked: `dev` at `783c682951a1c4d6a07a677779e0becab8bbbe2c` and latest release tag [`transports/v2.6.0`](https://github.com/maximhq/bifrost/releases/tag/transports/v2.6.0) at commit `172617bb045c6f43d697843c115e64f0155ed143` (tag object `d10f541617acbda020ddaffce31f8156c60d62d6`)
- Search terms used: `inferenceFileIDs`, `enforce_vk_object_ownership`, `ResponsesToolMessage`, `ResponsesFunctionToolCallOutputBlocks`, `ResponsesComputerToolCallOutput`, `function_call_output file_id`, `virtual key files`, `providerObjectReferences`
- Issues searched: Open and closed issues in `maximhq/bifrost` matching file ownership, virtual key isolation, `inferenceFileIDs`, and `#5025`
- Pull requests searched: Open, closed, and merged PRs in `maximhq/bifrost` touching `plugins/governance/providerjobs.go` ([#8292](https://github.com/maximhq/bifrost/pull/8292), [#8317](https://github.com/maximhq/bifrost/pull/8317), [#8427](https://github.com/maximhq/bifrost/pull/8427))
- Releases, changelog, and documentation searched: `transports/v2.6.0` tag, governance plugin docs, and `plugins/governance/providerjobs.go` commit history
- Relevant commits searched: `1f890c1d8` ([#8292](https://github.com/maximhq/bifrost/pull/8292)), `a535ff8de` ([#8317](https://github.com/maximhq/bifrost/pull/8317)), `24728305b` ([#8427](https://github.com/maximhq/bifrost/pull/8427))
- Matching links: [issue #5025](https://github.com/maximhq/bifrost/issues/5025), [PR #8292](https://github.com/maximhq/bifrost/pull/8292), [PR #8317](https://github.com/maximhq/bifrost/pull/8317), [PR #8427](https://github.com/maximhq/bifrost/pull/8427)

Classification:

- [x] Novel
- [ ] Duplicate, open and still reproducible
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

Parent issue [#5025](https://github.com/maximhq/bifrost/issues/5025) reported the general lack of virtual-key file isolation, and merged PRs [#8292](https://github.com/maximhq/bifrost/pull/8292), [#8317](https://github.com/maximhq/bifrost/pull/8317), and [#8427](https://github.com/maximhq/bifrost/pull/8427) landed `enforce_vk_object_ownership` and `inferenceFileIDs` to enforce per-VK file ownership across File APIs and inference requests. No upstream issue, pull request, test, or discussion identifies the bypass in `inferenceFileIDs` when `file_id` is passed inside tool outputs (`ResponsesToolMessage`: `function_call_output`, `custom_tool_call_output`, `computer_call_output`, or Anthropic `tool_result` converted via `ToBifrostResponsesRequest`).

## Test

`crates/harness/src/checks.rs` (`virtual_key_file_ownership_enforced_across_tool_outputs`) and `crates/harness/tests/conformance.rs` (`bifrost_148_*`) replay the recorded transcripts and assert the invariant: whenever a client request authenticated with virtual key `V` references a provider `file_id` owned by a different virtual key `W != V` in any request position (including `function_call_output`, `computer_call_output`, or Anthropic `tool_result`), the gateway must reject the request and must never forward the foreign `file_id` to the upstream provider.

## Frozen invariant

- Issue writeup: `issues/148-bifrost-vk-file-ownership-tool-output-bypass/README.md`
- Checker added or updated: `virtual_key_file_ownership_enforced_across_tool_outputs` in `crates/harness/src/checks.rs`
- Conformance test added or updated: `bifrost_148_*` tests in `crates/harness/tests/conformance.rs`
- Why the checker tests the invariant rather than one implementation detail: It parses the raw HTTP client request and response plus any forwarded upstream exchange, extracts every referenced `file_id` across URL paths, user messages, and tool outputs in both OpenAI Responses and Anthropic Messages dialects, compares each `file_id`'s owner against the caller's virtual key header, and flags a violation whenever an unauthorized `file_id` receives a 2xx response or reaches the upstream provider.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `BIFROST_SOURCE=/tmp/bifrost-148 BIFROST_BIN=/tmp/bifrost-148-http python3 transcripts/148/reproduce.py` | PASS, 5/5 bypass across 3 tool-output cells |
| Control | Same command, 3 cross-VK controls (`GET /v1/files/{id}`, Responses user message, Anthropic user message) and 1 owner tool-output control | PASS, 5/5 each |
| Harness | `cargo test --workspace` | PASS |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS |

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized response is committed.
- [x] Only environment variable names appear in commands and documentation.
- [x] The pull request contains one finding.
- [x] Unrelated generated files and local state are excluded.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS
- Overall: ACCEPT

## Independent review

Run `.github/agents/kairo-reproduction-reviewer.agent.md` against this pull request. Approval is blocked until the reviewer independently reruns the critical path and all three gates pass.
