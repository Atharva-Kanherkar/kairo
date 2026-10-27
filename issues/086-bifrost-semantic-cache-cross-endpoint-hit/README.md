# 086, Bifrost direct cache serves a Chat Completions entry to a Responses request, and the reverse

- **Upstream**: [maximhq/bifrost](https://github.com/maximhq/bifrost), no
  matching issue or pull request found on 2026-09-25
- **Tool under test**: Bifrost HTTP transport
  [`transports/v2.2.3`](https://github.com/maximhq/bifrost/releases/tag/transports%2Fv2.2.3)
  at commit
  [`411d62b28b03b03bd3b4025b2cfab50af45f05f4`](https://github.com/maximhq/bifrost/commit/411d62b28b03b03bd3b4025b2cfab50af45f05f4),
  core `1.10.2`, `semantic_cache` plugin `1.6.6`, built locally and reporting
  `v2.2.3`. Also reproduced on the current default branch `dev` at
  [`cefde78ba0e574f03da111e3fdcb9f4286e5ccc4`](https://github.com/maximhq/bifrost/commit/cefde78ba0e574f03da111e3fdcb9f4286e5ccc4)
- **Reproduced**: 2026-09-25 on macOS arm64. Public `POST /v1/chat/completions`
  and `POST /v1/responses`, OpenAI dialect on both sides, provider `openai`
  pointed at a deterministic local OpenAI-compatible upstream, model
  `openai/gpt-4o-mini`, the documented direct-only `semantic_cache`
  configuration (`dimension: 1`, `ttl: 5m`, `cache_by_model` and
  `cache_by_provider` true), Qdrant 1.19.1 as the vector store, and the
  documented `x-bf-cache-key` header. Chromem, the in-process store, gives the
  same result

## What breaks

With Bifrost's exact-match cache enabled (the UI calls it **Local Cache**,
mode **Direct only**), a Chat Completions request and a Responses API request
that carry the same user text under one cache key get the same cache entry.
Whichever arrives second is answered from the entry the other one wrote, and
never reaches the provider:

| First call | Second call | What the second caller receives |
|---|---|---|
| `POST /v1/chat/completions` | `POST /v1/responses` | `200 OK`, `Content-Length: 5`, body `null` |
| `POST /v1/responses` | `POST /v1/chat/completions` | `200 OK`, body `null` |
| chat stream | Responses stream | `chat.completion.chunk` frames and `[DONE]`; no `response.*` event at all |
| Responses stream | chat stream | `response.*` events; no `chat.completion.chunk` at all |

The provider is not called, so a retry does not help: three repeated Responses
calls after one chat call all got `null`, 5/5. The entry is kept for the
configured TTL, so later callers on the other API are expected to fail the same
way until it expires; that duration was not measured.

The people this hurts are teams that put Bifrost's documented cache in front of
more than one client stack. The OpenAI Agents SDK and the `openai` SDK's
`responses.create(input=...)` use the Responses API; most other code still
calls Chat Completions. When both send the same prompt under a deployment-wide
`default_cache_key` or a shared per-tenant key, the second one breaks:

- An OpenAI Agents SDK run raises `AttributeError: 'NoneType' object has no
  attribute 'usage'`, 5/5.
- `openai` `chat.completions.create()` returns `None` instead of a chat
  completion when the entry came from the Responses API, so the caller's next
  attribute access raises `AttributeError`, 5/5.
- `openai` `responses.stream()` raises ``RuntimeError: Expected to have
  received `response.created` before `None` ``, 5/5.
- A streaming Chat Completions client gets nine chunks and an empty answer, with
  no error, 5/5.

## Wire evidence

All captures are under [`transcripts/086/`](../../transcripts/086/). The
five-run matrix is in [`results.json`](../../transcripts/086/results.json) and
the SDK results are in
[`consumer-results.json`](../../transcripts/086/consumer-results.json).

| Cell | First call | Second call | Second call wrong family | Upstream calls per run |
|---|---|---|---:|---|
| `chat_then_responses` | chat | Responses, `input` string | 5/5 | 1, 1, 1, 1, 1 |
| `responses_then_chat` | Responses, `input` string | chat | 5/5 | 1, 1, 1, 1, 1 |
| `chat_stream_then_responses_stream` | chat stream | Responses stream | 5/5 | 1, 1, 1, 1, 1 |
| `responses_stream_then_chat_stream` | Responses stream | chat stream | 5/5 | 1, 1, 1, 1, 1 |
| `control_chat_then_chat` | chat | chat | 0/5 | 1, 1, 1, 1, 1 (correct hit) |
| `control_responses_then_responses` | Responses | Responses | 0/5 | 1, 1, 1, 1, 1 (correct hit) |
| `control_no_cache_key` | chat | Responses, no `x-bf-cache-key` | 0/5 | 2, 2, 2, 2, 2 |
| `control_typed_responses_item` | chat | Responses, `input` item with `"type":"message"` | 0/5 | 2, 2, 2, 2, 2 |

First-run bytes for the non-streaming violation:

- [`chat_then_responses-first-client-request.http`](../../transcripts/086/chat_then_responses-first-client-request.http)
  and
  [`chat_then_responses-first-client-response.http`](../../transcripts/086/chat_then_responses-first-client-response.http):
  the chat call and its `chat.completion` answer
- [`upstream/upstream-006-request.http`](../../transcripts/086/upstream/upstream-006-request.http):
  the only upstream request for that run, `POST /v1/chat/completions`
- [`chat_then_responses-second-client-request.http`](../../transcripts/086/chat_then_responses-second-client-request.http)
  and
  [`chat_then_responses-second-client-response.http`](../../transcripts/086/chat_then_responses-second-client-response.http):
  the Responses call and its `200 OK` `null` answer

For the streaming violation, Bifrost's own metadata shows the mechanism. The
chat stream in
[`chat_stream_then_responses_stream-first-client-response.http`](../../transcripts/086/chat_stream_then_responses_stream-first-client-response.http)
writes `"cache_debug":{"cache_hit":false,"cache_id":"<id>"}`, and the
Responses stream in
[`chat_stream_then_responses_stream-second-client-response.http`](../../transcripts/086/chat_stream_then_responses_stream-second-client-response.http)
replays those chat chunks with `"cache_hit":true,"cache_id":"<same id>","hit_type":"direct"`.

The persistence cell repeats the Responses call three times after one chat
call. All three repeats get `null` in 5/5 runs and the upstream sees only the
chat call (`results.json`, key `persistence`). Raw bytes for every run are in
`runs/`, every upstream exchange is in `upstream/`, and every SDK exchange is
in `sdk/`.

No provider credential was used. The configured provider key is synthetic and
exists only in a temporary runtime directory; captured authorization is
replaced inline with `<SYNTHETIC_PROVIDER_KEY>`. The SDK client key is an
unused placeholder, replaced inline with `<UNUSED_CLIENT_KEY>`.

## Root cause

All links are at the tested commit `411d62b2`. The published
`plugins/semanticcache` `v1.6.6` module that the release build resolves is
identical to that tree apart from its `LICENSE` file.

1. The direct lookup key has no request family.
   [`generateRequestHash`](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/search.go#L200-L210)
   hashes only `{input, params}`, and
   [`generateDirectCacheID`](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/search.go#L216-L239)
   adds only the cache key, provider, and model.
2. The two families produce the same hash input.
   [`buildRequestMetadataForCaching`](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/utils.go#L194-L263)
   records only the stream flag plus parameters, and even maps Chat
   `max_completion_tokens` and Responses `max_output_tokens` to the same
   `max_tokens` name
   ([L772](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/utils.go#L772),
   [L827](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/utils.go#L827)).
   [`getNormalizedInputForCaching`](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/utils.go#L635-L710)
   serializes a chat user message and an untyped Responses user message to the
   same JSON, `[{"role":"user","content":"..."}]`. The Responses handler builds
   exactly that message from a string `input`
   ([`inference.go#L1119-L1131`](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/transports/bifrost-http/handlers/inference.go#L1119-L1131)).
3. A hit is returned without checking what it holds.
   [`performDirectSearch`](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/search.go#L23-L52)
   fetches by ID ("a Get-by-ID is sufficient"), and
   [`buildResponseFromResult`](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/search.go#L248-L297)
   only rejects a stream versus non-stream mismatch
   ([L294](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/search.go#L294)).
4. Core then returns the typed field for the requested API.
   [`core/bifrost.go#L944`](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/core/bifrost.go#L944)
   returns `response.ResponsesResponse` and
   [`#L842`](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/core/bifrost.go#L842)
   returns `response.ChatResponse`. The field for the other family is nil, and
   the handler serializes it as `null`. Streams are replayed chunk by chunk, so
   the client receives the other family's frames.

The Go probe in
[`source_probe_test.go`](../../transcripts/086/source_probe_test.go) calls the
plugin's own functions. It gets the same hash, `c6df20b6e8f5d200`, for a chat
`user` message and an untyped Responses `user` message with the same text, and
a different hash once the Responses item carries `"type":"message"`, which
matches the `control_typed_responses_item` cell. It fails on the defect in five
separate runs on `v2.2.3` and five on `dev`
([`source-probe-5runs.txt`](../../transcripts/086/source-probe-5runs.txt)).

One-sentence fix: add the request family to the direct cache key, or treat a
stored entry whose family differs from the request as a miss, the same way the
plugin already treats a stream versus non-stream mismatch.

## Gate 1: correctness

### Exact reproduction

```bash
git clone https://github.com/maximhq/bifrost.git /tmp/bifrost-086
cd /tmp/bifrost-086
git checkout 411d62b28b03b03bd3b4025b2cfab50af45f05f4
mkdir -p transports/bifrost-http/ui
printf '<!doctype html><title>embed stub</title>\n' > transports/bifrost-http/ui/index.html
(cd transports/bifrost-http && GOWORK=off go build \
  -ldflags '-X main.Version=v2.2.3' -o /tmp/bifrost-086-http .)

# Qdrant 1.19.1, the official macOS arm64 release asset
gh release download v1.19.1 -R qdrant/qdrant -p 'qdrant-aarch64-apple-darwin.tar.gz'
tar xzf qdrant-aarch64-apple-darwin.tar.gz   # sha256 of the archive: e060209d...522fe4

# SDK consumer environment
uv venv -p 3.12 /tmp/kairo-086-venv
VIRTUAL_ENV=/tmp/kairo-086-venv uv pip install 'openai==3.19.2' 'openai-agents==0.22.3'

cd /path/to/kairo
BIFROST_SOURCE=/tmp/bifrost-086 BIFROST_BIN=/tmp/bifrost-086-http \
QDRANT_BIN=/path/to/qdrant CONSUMER_PYTHON=/tmp/kairo-086-venv/bin/python \
python3 transcripts/086/reproduce.py --runs 5
```

`GOWORK=off` builds the transport against the module versions its `go.mod`
pins (core `v1.10.2`, `semanticcache` `v1.6.6`), which is how a release binary
resolves them. The embedded React UI is not in the source checkout; the
one-line HTML file only satisfies Go's compile-time embed requirement and is
outside the request path. The gateway, plugin, vector store client, and public
routes are real code from the pinned tag.

The runner checks the commit, the cleanliness of `plugins/semanticcache`,
`core`, and `framework`, and the runtime version. It starts the upstream,
Qdrant, and the gateway with an enabled SQLite config store. Before measuring,
it waits until a same-family chat pair is served from cache, so the first run
is not a cold-store miss. It then writes reviewer-owned evidence to a fresh
temporary directory. `--store chromem` uses the in-process store instead of
Qdrant. For current `dev`, build commit `cefde78b` the same way with
`make setup-workspace` and `-X main.Version=dev-cefde78b`.

- Expected behavior: a cache hit on `/v1/responses` returns a Responses object
  and a hit on `/v1/chat/completions` returns a chat completion. An entry
  written by the other API is a miss.
- Observed behavior: the other API's entry is served. Non-streaming callers get
  `200 OK` with `null`; streaming callers get the other API's frames.
- Raw request evidence: `*-client-request.http` for every call
- Raw response evidence: `*-client-response.http` for every call
- Forwarded-request evidence: `upstream/`, one upstream call per violation run
- Reproduction rate: 5/5 in each of the four violation cells on `v2.2.3` with
  Qdrant, on `v2.2.3` with Chromem
  ([`matrix/v2.2.3-chromem-results.json`](../../transcripts/086/matrix/v2.2.3-chromem-results.json)),
  and on `dev` `cefde78b` with Qdrant
  ([`matrix/dev-cefde78b-qdrant-results.json`](../../transcripts/086/matrix/dev-cefde78b-qdrant-results.json))
- Smallest isolated trigger: one chat request and one Responses request with
  the same user text, the same model, the same cache key, the same stream flag,
  and no parameter that the two APIs name differently. The Responses input must
  be a string or an untyped message, which is what `responses.create(input=...)`
  and the Agents SDK send.

### Source-level falsifier

```bash
cp /path/to/kairo/transcripts/086/source_probe_test.go \
  /tmp/bifrost-086/plugins/semanticcache/zz_kairo086_probe_test.go
cd /tmp/bifrost-086/plugins/semanticcache
GOWORK=off go test -run TestKairo086DirectHashIgnoresRequestFamily -count=1 -v .
```

The probe exits nonzero while the two families share a hash. It is a unit
check of the key alone and needs no network, store, or gateway.

### Control

- `control_chat_then_chat` and `control_responses_then_responses` keep the
  same key and text on one API. The second call is a correct cache hit, 5/5,
  so the cache is engaged and works within one family.
- `control_no_cache_key` sends the same pair without `x-bf-cache-key`. Both
  calls reach the upstream and both answers are correct, 5/5.
- `control_typed_responses_item` changes only the Responses input to an item
  with `"type":"message"`. The hash differs, the call reaches the upstream, and
  the answer is correct, 5/5.

Together these attribute the failure to the cache key, not to the upstream,
the provider adapter, the SDKs, or the request shapes.

### False-positive checks

- [x] Tested the exact claim: one API family served the other's cache entry.
- [x] Pinned the latest HTTP release `v2.2.3` and current `dev` `cefde78b`.
- [x] Used the documented direct-only config, header, and a documented store.
- [x] Used valid requests in the shapes the official SDKs send.
- [x] Removed model nondeterminism with a deterministic local upstream.
- [x] Showed the failure is not created by the mock: the upstream receives one
      request per violation run and never sees the second call.
- [x] Sanitized all recorded evidence; no provider credential was used.

One disclosed timing detail: the first write into a fresh Qdrant collection can
land after the one-second gap the runner leaves between the two calls. A first
attempt without the warm-up step recorded 4/5 on `chat_then_responses` for that
reason. The warm-up needed two attempts in the final run before the first
same-family hit. After it, every cell is 5/5.

## Gate 2: usefulness

### Who gets bitten

- **Affected user**: a team running Bifrost's documented direct cache in front
  of services that use different OpenAI APIs, for example the OpenAI Agents
  SDK or `responses.create()` next to Chat Completions code, or one
  application moving from Chat Completions to Responses
- **Real workflow**: repeated prompts, which is the reason to enable an
  exact-match cache, sent by both APIs under one cache partition: the
  deployment-wide `default_cache_key`, or a tenant or application key shared by
  those services
- **Preconditions and frequency**: same model, same normalized text
  (lowercased and trimmed before hashing), same stream flag, within the TTL.
  Once those hold, the failure is 5/5. Production frequency was not measured.

### Observable consequence

`service A asks a question through Chat Completions -> Bifrost caches the chat
completion -> service B asks the same question through the Responses API ->
Bifrost serves the chat entry -> service B receives 200 null -> the agent run
crashes`

- Consumer-boundary demonstration: [`consumer.py`](../../transcripts/086/consumer.py)
  drives the gateway with `openai` 3.19.2 and `openai-agents` 0.22.3 and saves
  every SDK exchange under `sdk/`.

| Consumer cell | Result, 5 runs | Control |
|---|---|---|
| Agents SDK `Runner.run` after a chat call | `AttributeError` 5/5 | Agents SDK alone: correct answer 5/5 |
| `chat.completions.create` after an Agents SDK run | returns `None`, `AttributeError` 5/5 | same pair without a cache key: correct 5/5 |
| `responses.stream` after a chat stream | `RuntimeError` 5/5 | |
| chat stream after `responses.stream` | 9 chunks, empty text, no error, 5/5 | |

- Measured impact: every consumer cell fails 5/5 with one upstream call per
  run; the controls succeed 5/5. Three repeated Responses calls after one chat
  call all get `null`, 5/5.
- Inferred impact, not measured: agent frameworks built on these SDKs fail the
  same way, and the entry keeps failing for the rest of its TTL (5 minutes in
  the documented example) for every caller in that partition.

### Bug or not

- **Expected behavior is the intended spec**: the
  [semantic caching documentation](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/docs/features/semantic-caching.mdx#L60)
  lists chat completions and the Responses API as separately cached request
  types, and says an identical request "is served instantly". The plugin's own
  [`TestResponsesAPIBasicFunctionality`](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/plugin_responses_test.go#L64-L66)
  treats a nil Responses object on a cache hit as invalid. The
  [direct-only section](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/docs/features/semantic-caching.mdx#L360)
  says the hash covers "normalized input, parameters, and stream flag". Read
  literally that allows the collision, but no documentation, example, test, or
  UI says a Responses caller should receive a chat completion or `null`. Both
  public routes promise their own object type.
- **Maintainer ruling**: none found. The
  [plugin refactor, #3210](https://github.com/maximhq/bifrost/pull/3210), which
  introduced the shared `max_tokens` name, does not discuss sharing entries
  across APIs. The plugin already treats a stream versus non-stream shape
  mismatch as a miss because the "entry may be corrupt"
  ([L294](https://github.com/maximhq/bifrost/blob/411d62b28b03b03bd3b4025b2cfab50af45f05f4/plugins/semanticcache/search.go#L294)),
  which is the same kind of guard this case lacks.
- **Supported usage**: direct-only mode, the header, `default_cache_key`, and
  Qdrant are documented; the plugin config is the documentation's own example;
  both requests are default SDK shapes. Bifrost auth is not involved.
- **Boundary crossed**: not a disclosure claim. The failure is a successful
  response of the wrong API family, which the caller cannot use.
- **Maintainer fix**: include the request family in the direct cache key, or
  reject a cached entry whose family does not match the request.
- **Label**: `bug`

## Gate 3: upstream status

Checked 2026-09-25. Latest HTTP transport release:
[`transports/v2.2.3`](https://github.com/maximhq/bifrost/releases/tag/transports%2Fv2.2.3),
2026-09-24. Current default branch `dev` head
[`cefde78b`](https://github.com/maximhq/bifrost/commit/cefde78ba0e574f03da111e3fdcb9f4286e5ccc4)
has the same `generateRequestHash` and reproduces 5/5. The plugin changelog has
no entry about request types or cross-API hits.

Searches covered open and closed issues and open, closed, and merged pull
requests, plus the plugin changelog, documentation, and tests. Terms:

```text
"semantic cache"
semanticcache
semantic_cache
generateRequestHash
"semantic cache responses null"
"cache responses chat completions"
"semantic_cache request type"
"cache key request type"
"cache hit null body"
"direct cache responses"
"semantic cache wrong format"
"semantic cache Responses API"
"x-bf-cache-key responses"
```

Nearest matches, none of them this defect:

- [#5769](https://github.com/maximhq/bifrost/pull/5769), open PR: scopes cache
  keys to the virtual key, a tenant boundary, not the request family
- [#6101](https://github.com/maximhq/bifrost/pull/6101), closed PR: isolates
  direct-key namespaces by credential
- [#7450](https://github.com/maximhq/bifrost/issues/7450), closed: a panic while
  marshaling `/v1/responses` cache writes
- [#7233](https://github.com/maximhq/bifrost/issues/7233), closed: a race in
  direct-only cache serialization
- [#7501](https://github.com/maximhq/bifrost/issues/7501), open: the semantic
  threshold serves wrong answers for near-miss prompts, a different path
- [#522](https://github.com/maximhq/bifrost/pull/522),
  [#2142](https://github.com/maximhq/bifrost/pull/2142),
  [#3050](https://github.com/maximhq/bifrost/pull/3050): hash determinism fixes

Classification: `novel`.

## Frozen invariant

- Issue writeup: this file
- Checker: `endpoint_response_family_preserved` in
  `crates/harness/src/checks.rs`
- Conformance coverage: four issue-086 tests in
  `crates/harness/tests/conformance.rs`, plus a unit test for the checker
- Invariant: a successful response from `/v1/chat/completions` or
  `/v1/responses` carries that endpoint's own family, whichever layer produced
  it. It reads only the public OpenAI `object` and `type` discriminators, so it
  does not depend on Bifrost's cache design.
- Vacuity guards: non-2xx, unparseable, and unknown-endpoint captures, a
  Responses stream with no terminal event, and a correct same-family hit
  checked against the other endpoint all score violations

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `python3 transcripts/086/reproduce.py --runs 5` | PASS, four violation cells 5/5 on `v2.2.3` Qdrant, `v2.2.3` Chromem, and `dev` Qdrant |
| Controls | same runner | PASS, all four controls conformant 5/5 |
| Consumer boundary | same runner with `CONSUMER_PYTHON` | PASS, four SDK failure cells 5/5, both SDK controls healthy 5/5 |
| Focused harness | `cargo test -p kairo --test conformance bifrost_direct_cache` | PASS, 4/4 |
| Workspace | `cargo test --workspace` | PASS |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS |

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| The second call is served from the other API's entry | High | one upstream call per run, and Bifrost's `cache_debug` shows the same `cache_id` with `hit_type: direct` |
| The caller receives `null` or foreign frames | High | raw client responses, 5/5 per cell on three configurations |
| The key has no request family | High | source path plus the Go hash probe plus the typed-item control |
| Real SDK and agent failures follow | High | `openai` 3.19.2 and `openai-agents` 0.22.3, 5/5 against healthy controls |
| Semantic (embedding) mode shares the defect | Not claimed | only direct-only mode was run |
| Production frequency | Not measured | depends on how often two APIs send the same text under one key |

## Test

`crates/harness/tests/conformance.rs` freezes the four violation captures, all
four controls, the first uncached response of every cell, the five-run matrix
on three configurations, the fifteen persistence repeats, and the consumer
results. The checker would flip to
conformant for the violation captures only if the second caller received its
own API's object.
