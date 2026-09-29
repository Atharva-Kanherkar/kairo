# kairo

**Kairo is a dataset of real, reproducible bugs in production AI infrastructure, with executable environments, controls, exact failure traces, and verified fixes.**

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
| Reproduced issue folders | 65 |
| Gateways under test | LiteLLM, NVIDIA Switchyard, Bifrost, GoModel, AxonHub, any-llm, Dynamo, OGX, and agentgateway |
| Harness tests | 220 (179 conformance checks against recorded transcripts, 41 unit) |
<!-- kairo-counts:end -->

The 65 folders cover reproduced findings, multi-defect reports, and honest
negative results. Versions and reproduction outcomes are recorded per finding
in [`issues/SCOREBOARD.md`](issues/SCOREBOARD.md), including cited bugs that did
not reproduce.
Filed issues and fix pull requests are tracked in
[Upstream activity](#upstream-activity).

## Findings

Independent gateways violate the same small set of invariants, and a checker
written against one gateway's transcript catches the same defect in the others
without modification. That is the central result. The table groups every reproduced defect by the invariant it
violates. Each number links to the folder with the writeup, the bytes, and the
reproduction commands.

| Invariant | LiteLLM | Switchyard | Bifrost | GoModel | AxonHub | any-llm | OGX |
|---|---|---|---|---|---|---|---|
| Terminal reason survives translation (`tool_use`, `content_filter`, `max_tokens`, refusal) | [001](issues/001-anthropic-stream-toolcall-translation), [002](issues/002-litellm-ollama-toolcall-loss) | [010](issues/010-switchyard-content-filter-and-reorder) | [030](issues/030-bifrost-anthropic-stream-stop-reason), [034](issues/034-bifrost-erases-content-filter), [035](issues/035-bifrost-erases-truncation), [036](issues/036-bifrost-drops-refusal-content), [078](issues/078-bifrost-filter-stream-divergence) | | | | |
| One public stream has one response lifecycle and output namespace | [074](issues/074-litellm-mcp-responses-stream-collision) | | | | | | |
| A cached replay carries the requesting endpoint's API family | | | [086](issues/086-bifrost-semantic-cache-cross-endpoint-hit) | | | | |
| One client turn executes a side-effecting gateway tool at most once | [087](issues/087-litellm-mcp-retry-reexecutes-tool) | | | | | | |
| Request constraints survive (`disable_parallel_tool_use`, `stop_sequences`, `tools[].strict`, `output_format`) | [017](issues/017-parallel-tool-flag-dropped), [041](issues/041-litellm-drops-stop-sequences), [064](issues/064-litellm-drops-tool-strict) | [006](issues/006-switchyard-crossformat-losses), [017](issues/017-parallel-tool-flag-dropped), [040](issues/040-switchyard-drops-output-format), [065](issues/065-switchyard-responses-instruction-loss), [066](issues/066-switchyard-drops-tool-strict) | [031](issues/031-bifrost-drops-parallel-tool-flag), [032](issues/032-bifrost-drops-stop-sequences), [072](issues/072-bifrost-anthropic-tool-choice-any-leak) | [042](issues/042-gomodel-drops-output-format), [043](issues/043-gomodel-drops-parallel-tool-flag) | [051](issues/051-axonhub-drops-output-format) | [058](issues/058-any-llm-drops-parallel-tool-flag), [062](issues/062-any-llm-empty-schema-shell) | [081](issues/081-ogx-messages-translation-losses) |
| Provider-owned request fields survive pass-through | [084](issues/084-litellm-openai-passthrough-drops-id) | | | | | | |
| Content blocks survive (refusal, `is_error`, image and document blocks in tool results and user turns) | [006](issues/006-switchyard-crossformat-losses), [007](issues/007-switchyard-toolresult-multimodal-stringified), [018](issues/018-user-document-dropped), [067](issues/067-litellm-drops-refusal-content) | [006](issues/006-switchyard-crossformat-losses), [007](issues/007-switchyard-toolresult-multimodal-stringified), [018](issues/018-user-document-dropped), [068](issues/068-switchyard-drops-refusal-content), [069](issues/069-switchyard-responses-refusal) | | | | [059](issues/059-any-llm-drops-is-error), [060](issues/060-any-llm-drops-toolresult-image), [061](issues/061-any-llm-drops-toolresult-document) | |
| Assistant history survives replay (`thinking` blocks, signatures, and stored continuations) | [016](issues/016-thinking-history-lost) (leaked as visible text) | [016](issues/016-thinking-history-lost) (dropped), [083](issues/083-switchyard-conversation-continuation-loss) | [033](issues/033-bifrost-drops-thinking-history) | | | [057](issues/057-any-llm-drops-thinking-history) | |
| Tool-call ids round-trip | [004](issues/004-gemini-thought-signature) | [005](issues/005-switchyard-toolid-sanitizer) | [037](issues/037-bifrost-toolid-not-restored) | | | | |
| Nothing is invented (empty text blocks, phantom message items, `cache_control`) | [001](issues/001-anthropic-stream-toolcall-translation), [009](issues/009-litellm-responses-phantom-message) | [019](issues/019-switchyard-invents-prompt-cache), [045](issues/045-switchyard-empty-text-before-tooluse), [068](issues/068-switchyard-drops-refusal-content) | | | | | |
| Malformed input fails closed | [008](issues/008-litellm-messages-indexerror-crash) | | | | | | |
| Credentials stay behind their intended trust boundary | [020](issues/020-litellm-client-api-key), [024](issues/024-litellm-health-extra-headers), [026](issues/026-litellm-extra-headers-org), [028](issues/028-litellm-gemini-passthrough-upload-url), [071](issues/071-litellm-model-info-api-base-leak) | [023](issues/023-switchyard-forwards-org-api-key), [025](issues/025-switchyard-transport-query-key), [027](issues/027-switchyard-forwards-x-goog-api-key), [063](issues/063-switchyard-redirect-follows-x-api-key) | [077](issues/077-bifrost-custom-response-header-secret-leak), [079](issues/079-bifrost-forged-realtime-key-admission) | | | | |

The `disable_parallel_tool_use` flag is dropped by five of the six gateways
where that probe has been run. OGX is listed separately for its adaptive
thinking configuration loss.
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

## Upstream activity

Findings that hold up are filed with the maintainers, and most come with a fix
pull request. This is every issue and pull request filed in the projects kairo
tests, with its current state on GitHub and the finding it came from.

<!-- kairo-upstream:start -->
| Project | Issues open | Issues closed | PRs merged | PRs open | PRs closed |
|---|--:|--:|--:|--:|--:|
| [LiteLLM](https://github.com/BerriAI/litellm) | 4 | 3 | 2 | 2 | 0 |
| [NVIDIA Switchyard](https://github.com/NVIDIA-NeMo/Switchyard) | 2 | 9 | 3 | 2 | 1 |
| [Bifrost](https://github.com/maximhq/bifrost) | 2 | 3 | 3 | 2 | 0 |
| [Dynamo](https://github.com/ai-dynamo/dynamo) | 1 | 0 | 0 | 1 | 0 |
| [OGX](https://github.com/ogx-ai/ogx) | 0 | 1 | 1 | 0 | 0 |
| [agentgateway](https://github.com/agentgateway/agentgateway) | 1 | 0 | 0 | 0 | 0 |
| [any-llm](https://github.com/mozilla-ai/any-llm) | 0 | 1 | 1 | 0 | 0 |
| [async-openai](https://github.com/64bit/async-openai) | 0 | 0 | 1 | 0 | 0 |
| **Total** | **10** | **17** | **11** | **7** | **1** |

🟢 open · 🟣 merged or completed · 🔴 PR closed unmerged · ⚪ issue closed as not planned. State checked 2026-09-29. Refresh with `python3 tools/update-upstream-log.py`.

<details open>
<summary><b>LiteLLM</b> (7 issues, 4 pull requests)</summary>

**Pull requests**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟢 open | [#43159](https://github.com/BerriAI/litellm/pull/43159) | fix(router): stop retries and fallbacks from re-running executed MCP tools | [087](issues/087-litellm-mcp-retry-reexecutes-tool) | 2026-09-25 |
| 🟢 open | [#42958](https://github.com/BerriAI/litellm/pull/42958) | fix(router): stop Responses fallback after delivered output | [085](issues/085-litellm-responses-fallback-replays-tool-call) | 2026-09-24 |
| 🟣 merged | [#40121](https://github.com/BerriAI/litellm/pull/40121) | fix(responses): stream one lifecycle across MCP auto-execute rounds | [074](issues/074-litellm-mcp-responses-stream-collision) | 2026-09-07 |
| 🟣 merged | [#39723](https://github.com/BerriAI/litellm/pull/39723) | fix(anthropic_responses): preserve Responses refusal blocks in Anthropic messages translation | [067](issues/067-litellm-drops-refusal-content) | 2026-09-04 |

**Issues**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟢 open | [#43153](https://github.com/BerriAI/litellm/issues/43153) | Router retries re-execute auto-approved MCP tools after a failed follow-up model call | [087](issues/087-litellm-mcp-retry-reexecutes-tool) | 2026-09-25 |
| 🟢 open | [#42955](https://github.com/BerriAI/litellm/issues/42955) | Responses streaming fallback replays a delivered tool call | [085](issues/085-litellm-responses-fallback-replays-tool-call) | 2026-09-24 |
| 🟣 completed | [#40118](https://github.com/BerriAI/litellm/issues/40118) | Streaming /v1/responses with an auto-executed MCP tool emits two response lifecycles in one stream, OpenAI SDK responses.stream() raises AssertionError | [074](issues/074-litellm-mcp-responses-stream-collision) | 2026-09-07 |
| 🟣 completed | [#39721](https://github.com/BerriAI/litellm/issues/39721) | Anthropic /v1/messages erases OpenAI Responses refusal blocks into empty content array | [067](issues/067-litellm-drops-refusal-content) | 2026-09-04 |
| 🟢 open | [#37118](https://github.com/BerriAI/litellm/issues/37118) | /v1/messages drops stop_sequences | [041](issues/041-litellm-drops-stop-sequences) | 2026-08-16 |
| 🟣 completed | [#36898](https://github.com/BerriAI/litellm/issues/36898) | GET /health returns extra_headers and aws_session_token in plaintext | [024](issues/024-litellm-health-extra-headers) | 2026-08-14 |
| 🟢 open | [#36794](https://github.com/BerriAI/litellm/issues/36794) | proxy uses request-body api_key without allow_client_side_credentials | [020](issues/020-litellm-client-api-key) | 2026-08-13 |

</details>

<details open>
<summary><b>NVIDIA Switchyard</b> (11 issues, 6 pull requests)</summary>

**Pull requests**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟢 open | [#803](https://github.com/NVIDIA-NeMo/Switchyard/pull/803) | fix(llm-client): store canonical history under the conversation id | [083](issues/083-switchyard-conversation-continuation-loss) | 2026-09-20 |
| 🟢 open | [#623](https://github.com/NVIDIA-NeMo/Switchyard/pull/623) | fix(translation): preserve OpenAI Chat refusal text | [068](issues/068-switchyard-drops-refusal-content) | 2026-09-04 |
| 🔴 closed | [#544](https://github.com/NVIDIA-NeMo/Switchyard/pull/544) | fix(llm-client): follow only same-origin redirects to keep credentials on origin | [063](issues/063-switchyard-redirect-follows-x-api-key) | 2026-08-25 |
| 🟣 merged | [#523](https://github.com/NVIDIA-NeMo/Switchyard/pull/523) | fix(translation): keep Responses inline system and developer roles | [065](issues/065-switchyard-responses-instruction-loss) | 2026-08-22 |
| 🟣 merged | [#420](https://github.com/NVIDIA-NeMo/Switchyard/pull/420) | fix(client): strip api-key and OpenAI org/project headers before forwarding | [023](issues/023-switchyard-forwards-org-api-key) | 2026-08-14 |
| 🟣 merged | [#370](https://github.com/NVIDIA-NeMo/Switchyard/pull/370) | fix(translation): report content filter stops as Anthropic refusal | [010](issues/010-switchyard-content-filter-and-reorder) | 2026-08-11 |

**Issues**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟢 open | [#802](https://github.com/NVIDIA-NeMo/Switchyard/issues/802) | Responses conversation continuation drops materialized cross-format history | [083](issues/083-switchyard-conversation-continuation-loss) | 2026-09-20 |
| 🟢 open | [#622](https://github.com/NVIDIA-NeMo/Switchyard/issues/622) | OpenAI-to-Anthropic translation erases structured refusal text and emits empty text block | [068](issues/068-switchyard-drops-refusal-content) | 2026-09-04 |
| 🟣 completed | [#577](https://github.com/NVIDIA-NeMo/Switchyard/issues/577) | switchyard drops anthropic tools[].strict when translating to openai chat | [066](issues/066-switchyard-drops-tool-strict) | 2026-08-29 |
| 🟣 completed | [#543](https://github.com/NVIDIA-NeMo/Switchyard/issues/543) | default upstream client follows cross-origin redirects while holding the deployment x-api-key | [063](issues/063-switchyard-redirect-follows-x-api-key) | 2026-08-25 |
| 🟣 completed | [#521](https://github.com/NVIDIA-NeMo/Switchyard/issues/521) | /v1/responses demotes system/developer input roles to user on the chat wire | [065](issues/065-switchyard-responses-instruction-loss) | 2026-08-22 |
| 🟣 completed | [#452](https://github.com/NVIDIA-NeMo/Switchyard/issues/452) | /v1/messages drops output_format / json_schema | [040](issues/040-switchyard-drops-output-format) | 2026-08-16 |
| 🟣 completed | [#423](https://github.com/NVIDIA-NeMo/Switchyard/issues/423) | transport 502 echoes base_url including ?key= | [025](issues/025-switchyard-transport-query-key) | 2026-08-14 |
| 🟣 completed | [#419](https://github.com/NVIDIA-NeMo/Switchyard/issues/419) | Client api-key and OpenAI org/project headers are forwarded upstream | [023](issues/023-switchyard-forwards-org-api-key) | 2026-08-14 |
| 🟣 completed | [#410](https://github.com/NVIDIA-NeMo/Switchyard/issues/410) | proxy forwards client x-* headers including x-goog-api-key to the upstream | [027](issues/027-switchyard-forwards-x-goog-api-key) | 2026-08-13 |
| 🟣 completed | [#380](https://github.com/NVIDIA-NeMo/Switchyard/issues/380) | image and document blocks in tool_result are serialized into a text string when translating to OpenAI Chat | [007](issues/007-switchyard-toolresult-multimodal-stringified) | 2026-08-12 |
| 🟣 completed | [#369](https://github.com/NVIDIA-NeMo/Switchyard/issues/369) | OpenAI content_filter is silently translated to Anthropic end_turn | [010](issues/010-switchyard-content-filter-and-reorder) | 2026-08-11 |

</details>

<details open>
<summary><b>Bifrost</b> (5 issues, 5 pull requests)</summary>

**Pull requests**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟢 open | [#7561](https://github.com/maximhq/bifrost/pull/7561) | fix(semanticcache): isolate cache entries by request family | [086](issues/086-bifrost-semantic-cache-cross-endpoint-hit) | 2026-09-25 |
| 🟢 open | [#7385](https://github.com/maximhq/bifrost/pull/7385) | preserve repeated MCP agent tool results | [082](issues/082-bifrost-agent-tool-result-collision) | 2026-09-21 |
| 🟣 merged | [#7121](https://github.com/maximhq/bifrost/pull/7121) | fix: filter credential-bearing provider response headers by classifier | [077](issues/077-bifrost-custom-response-header-secret-leak) | 2026-09-13 |
| 🟣 merged | [#7033](https://github.com/maximhq/bifrost/pull/7033) | Gemini provider - preserve inline image and audio data in chat completions | [075](issues/075-bifrost-gemini-inlinedata-dropped) | 2026-09-09 |
| 🟣 merged | [#6888](https://github.com/maximhq/bifrost/pull/6888) | fix: map Anthropic forced tool choice any to required on OpenAI egress | [072](issues/072-bifrost-anthropic-tool-choice-any-leak) | 2026-09-06 |

**Issues**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟢 open | [#7560](https://github.com/maximhq/bifrost/issues/7560) | semantic_cache serves a chat.completion entry to a /v1/responses caller because the direct cache key omits the request family | [086](issues/086-bifrost-semantic-cache-cross-endpoint-hit) | 2026-09-25 |
| 🟢 open | [#7383](https://github.com/maximhq/bifrost/issues/7383) | agent mode drops one result when the same auto-executable tool runs twice in one turn | [082](issues/082-bifrost-agent-tool-result-collision) | 2026-09-21 |
| 🟣 completed | [#7120](https://github.com/maximhq/bifrost/issues/7120) | Provider response-header filter ignores IsSensitiveHeader, forwarding credential-named headers to inference callers | [077](issues/077-bifrost-custom-response-header-secret-leak) | 2026-09-13 |
| 🟣 completed | [#7032](https://github.com/maximhq/bifrost/issues/7032) | Gemini image-generation output (inlineData) silently dropped on /v1/chat/completions, both unary and streaming | [075](issues/075-bifrost-gemini-inlinedata-dropped) | 2026-09-09 |
| 🟣 completed | [#6887](https://github.com/maximhq/bifrost/issues/6887) | Anthropic tool_choice {type: any} forwarded to OpenAI as "any" instead of "required" | [072](issues/072-bifrost-anthropic-tool-choice-any-leak) | 2026-09-06 |

</details>

<details open>
<summary><b>Dynamo</b> (1 issues, 1 pull requests)</summary>

**Pull requests**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟢 open | [#15101](https://github.com/ai-dynamo/dynamo/pull/15101) | fix(multimodal): preserve URL path case in ImageLoader cache key | [080](issues/080-dynamo-imageloader-lowercase-cache-key) | 2026-09-19 |

**Issues**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟢 open | [#15100](https://github.com/ai-dynamo/dynamo/issues/15100) | ImageLoader cache key lowercases the whole URL, returning the wrong image for case-differing paths | [080](issues/080-dynamo-imageloader-lowercase-cache-key) | 2026-09-19 |

</details>

<details open>
<summary><b>OGX</b> (1 issues, 1 pull requests)</summary>

**Pull requests**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟣 merged | [#6615](https://github.com/ogx-ai/ogx/pull/6615) | fix(anthropic): reject adaptive thinking in translation mode | [081](issues/081-ogx-messages-translation-losses) | 2026-09-22 |

**Issues**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟣 completed | [#6614](https://github.com/ogx-ai/ogx/issues/6614) | Anthropic to OpenAI translation silently drops thinking.type=adaptive and returns 200 | [081](issues/081-ogx-messages-translation-losses) | 2026-09-22 |

</details>

<details open>
<summary><b>agentgateway</b> (1 issues, 0 pull requests)</summary>

**Issues**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟢 open | [#3681](https://github.com/agentgateway/agentgateway/issues/3681) | it emits null tool_calls[].type on OpenAI streams, and openai-python's accumulator raises before the agent completes | [088](issues/088-agentgateway-toolid-type-null-breaks-openai-stream) | 2026-09-27 |

</details>

<details open>
<summary><b>any-llm</b> (1 issues, 1 pull requests)</summary>

**Pull requests**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟣 merged | [#1312](https://github.com/mozilla-ai/any-llm/pull/1312) | fix: preserve fields the anthropic messages bridge dropped on encode | [057](issues/057-any-llm-drops-thinking-history)-[062](issues/062-any-llm-empty-schema-shell) | 2026-08-18 |

**Issues**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟣 completed | [#1311](https://github.com/mozilla-ai/any-llm/issues/1311) | messages_compat drops thinking history, parallel flag, is_error, and multimodal tool results | [057](issues/057-any-llm-drops-thinking-history)-[062](issues/062-any-llm-empty-schema-shell) | 2026-08-18 |

</details>

<details open>
<summary><b>async-openai</b> (0 issues, 1 pull requests)</summary>

**Pull requests**

| State | # | Title | Finding | Opened |
|---|---|---|---|---|
| 🟣 merged | [#590](https://github.com/64bit/async-openai/pull/590) | fix(chat): skip_serializing_if on ChatCompletionMessageToolCallChunk optional fields | [088](issues/088-agentgateway-toolid-type-null-breaks-openai-stream) | 2026-09-27 |

</details>
<!-- kairo-upstream:end -->

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
tools/update-upstream-log.py       regenerates the Upstream activity block from gh
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
