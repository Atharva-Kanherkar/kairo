# 069, Switchyard loses structured refusal semantics on Responses output

- **Upstream**: [NVIDIA-NeMo/Switchyard#622](https://github.com/NVIDIA-NeMo/Switchyard/issues/622)
  is open and [pull request #623](https://github.com/NVIDIA-NeMo/Switchyard/pull/623)
  is a draft as of 2026-09-05. They cover refusal-text loss on Anthropic output,
  not the OpenAI Responses type loss recorded here.
- **Tool under test**: Switchyard `main` commit **`7a23989`** and pull request
  #623 head **`2765f46`**, both reporting `switchyard-server` **0.2.0**.
- **Reproduced**: 2026-09-04 on macOS arm64, Rust 1.96.1, with an
  `openai_chat` backend and a keyless deterministic capture upstream.

## What breaks

An OpenAI Responses client calls Switchyard's `/v1/responses` endpoint. The
configured OpenAI Chat backend returns the documented structured-refusal shape:

```json
{"message":{"role":"assistant","content":null,"refusal":"REFUSALPROBE cannot help"}}
```

Current Switchyard `main` returns a completed Responses object with an empty
`output_text` part in buffered mode and no output item in streaming mode. Pull
request #623 preserves the words, but still returns them as ordinary
`output_text` and `response.output_text.delta` data. Neither revision emits a
Responses `refusal` content part or `response.refusal.delta` and
`response.refusal.done` events.

This distinction is part of the Responses wire contract. A client that detects
refusals by their documented content type or stream events classifies every
translated refusal as an ordinary answer. The consumer check recorded in each
trial returned `classified_as_refusal: false` for all Responses calls and true
for the same-dialect Chat controls.

This is separate from issue 068. Issue 068 freezes the complete loss of refusal
text on OpenAI Chat to Anthropic translation. Issue 069 freezes the loss of the
machine-readable refusal type on OpenAI Chat to OpenAI Responses translation,
including the state that remains after the issue 068 fix in pull request #623.

## Wire evidence

Every JSONL line is one complete trial. It contains the raw client request body,
the raw request Switchyard sent upstream, the raw upstream response, the raw
client response, status and content type, and the consumer classification. No
credential was used or recorded.

- `transcripts/069/switchyard-main-responses-buffered.jsonl`: five current-main
  buffered violations. The refusal becomes empty `output_text` 5/5.
- `transcripts/069/switchyard-main-responses-stream.jsonl`: five current-main
  stream violations. Only `response.created` and `response.completed` appear
  5/5.
- `transcripts/069/switchyard-pr623-responses-buffered.jsonl`: five pull request
  #623 buffered violations. The refusal text becomes ordinary `output_text`
  5/5.
- `transcripts/069/switchyard-pr623-responses-stream.jsonl`: five pull request
  #623 stream violations. The refusal text becomes
  `response.output_text.delta` 5/5.
- `transcripts/069/switchyard-{main,pr623}-chat-{buffered,stream}-control.jsonl`:
  twenty same-process controls. The same upstream response remains byte-equal
  at the client boundary and retains `message.refusal` or `delta.refusal` 20/20.
- `transcripts/069/expected-responses-buffered.json` and
  `expected-responses-stream.sse`: minimal conformant Responses representations
  based on the OpenAI Responses schema.

| Target | Client path | Mode | Typed refusal detected | Result |
|---|---|---|---:|---|
| `7a23989` current main | `/v1/responses` | buffered | 0/5 | violation |
| `7a23989` current main | `/v1/responses` | streaming | 0/5 | violation |
| `2765f46` pull request #623 | `/v1/responses` | buffered | 0/5 | violation |
| `2765f46` pull request #623 | `/v1/responses` | streaming | 0/5 | violation |
| `7a23989` current main | `/v1/chat/completions` | buffered and streaming | 10/10 | control passes |
| `2765f46` pull request #623 | `/v1/chat/completions` | buffered and streaming | 10/10 | control passes |

## Root cause (if found)

On pull request #623, the buffered Chat decoder correctly creates
`ContentBlock::Refusal`, but `encode_responses_output` in
`crates/switchyard-translation/src/codecs/responses/buffered.rs` folds refusal
blocks into `text_from_blocks` and always writes the result as
`{"type":"output_text"}` at lines 1336 and 1355-1358.

The streaming path loses the distinction earlier. The pull request's Chat
decoder maps `delta.refusal` into the generic `LlmResponseChunk::TextDelta` at
`codecs/openai_chat/stream.rs:137-144`. The provider-neutral stream enum has no
refusal delta variant, and the Responses encoder maps every `TextDelta` to
`response.output_text` at `codecs/responses/stream.rs:238`.

Current `main` also omits the sibling Chat refusal during decoding, which
explains its empty output. Pull request #623 fixes that first loss but leaves the
Responses output type loss intact.

## Test

`responses_refusal_semantics_preserved` requires non-vacuous upstream refusal
evidence. It then checks the client dialect's protocol-level representation:

- buffered Responses must contain a matching `type: "refusal"` content part;
- streamed Responses must reconstruct the same text from
  `response.refusal.delta` and finish it with `response.refusal.done`.

The checker intentionally rejects a byte-for-byte refusal explanation carried
only as `output_text`. It tests whether the semantic signal survives, not a
Switchyard-specific implementation detail.

Replay the frozen evidence offline:

```bash
cargo test -p kairo responses_refusal
cargo test -p kairo --test conformance switchyard_main_drops_responses_refusal_semantics
cargo test -p kairo --test conformance switchyard_pr623_flattens_responses_refusal_to_output_text
cargo test -p kairo --test conformance switchyard_chat_route_preserves_the_same_refusal_control
```

Rebuild and rerun the real server path:

```bash
git clone --filter=blob:none https://github.com/NVIDIA-NeMo/Switchyard.git /tmp/switchyard-069
rustup toolchain install 1.96.1
cd /tmp/switchyard-069
git checkout --detach 7a23989cbe18f1c6c67ee03684ce76bd5901a27d
cd /path/to/kairo
python3 transcripts/069/reproduce.py \
  --switchyard-source /tmp/switchyard-069 \
  --label main \
  --expected-commit 7a23989cbe18f1c6c67ee03684ce76bd5901a27d

cd /tmp/switchyard-069
git checkout --detach 2765f46972bf89a96beb5b2158b0fc56a3a72288
cd /path/to/kairo
python3 transcripts/069/reproduce.py \
  --switchyard-source /tmp/switchyard-069 \
  --label pr623 \
  --expected-commit 2765f46972bf89a96beb5b2158b0fc56a3a72288
```

The reproducer requires a clean tracked checkout, resolves the Cargo and Rust
compiler binaries for toolchain 1.96.1 through `rustup`, builds with `--locked`,
checks the exact commit and reported version, and records the binary SHA-256 and
compiler version in every trial.

## Three-gate review

### Gate 1: correctness

The exact OpenAI Responses and OpenAI Chat endpoints were exercised through the
real `switchyard-server` public HTTP entry point. The model, prompt, local
upstream, and returned refusal bytes were identical. Only the client endpoint
changed. Every upstream request reached `/v1/chat/completions` and every
upstream response contained the same structured refusal.

The input request is valid because Switchyard translated and forwarded it and
returned HTTP 200. Model nondeterminism is irrelevant because the upstream bytes
are fixed. The capture is not inventing a nonstandard provider shape: OpenAI's
Chat schema defines `message.refusal`, OpenAI's Responses schema defines typed
refusal output, and pull request #623 independently records live
`gpt-4o-2024-08-06` responses with the same `content: null` and populated
`message.refusal` shape.

Result: **PASS**.

### Gate 2: usefulness

- **Affected user**: an application, evaluator, or guardrail using the OpenAI
  Responses API through Switchyard with an OpenAI Chat backend.
- **Workflow**: send a Responses request, receive an upstream policy refusal,
  and branch on the documented refusal content type or refusal stream events.
- **Observable consequence**: the recorded consumer sees zero typed refusals and
  classifies all translated refusals as ordinary output or empty success.
- **Measured impact**: 20/20 Responses trials missed the refusal type across the
  two pinned revisions and modes. All 20 Chat controls detected it.
- **Inferred impact**: refusal analytics, evaluation scoring, retry policy, and
  safety handling can record false negatives whenever this exact backend shape
  occurs. Real-world refusal frequency was not measured.

Result: **PASS**.

### Gate 3: upstream status

Checked 2026-09-05 against Switchyard `main` `7a23989`, release 0.2.0, and pull
request #623 head `2765f46`.

Searches covered open and closed issues and pull requests for
`message.refusal`, `delta.refusal`, `response.refusal.delta`,
`response.refusal.done`, `output_text`, `Responses refusal`, and `refusal`.
The repository code, `CHANGELOG.md`, releases, relevant commits, pull request
review comments, and official OpenAI Chat and Responses API documentation were
also checked.

Relevant links:

- [Switchyard issue #622](https://github.com/NVIDIA-NeMo/Switchyard/issues/622)
- [Switchyard pull request #623](https://github.com/NVIDIA-NeMo/Switchyard/pull/623)
- [Switchyard pull request #370](https://github.com/NVIDIA-NeMo/Switchyard/pull/370)
- [OpenAI Chat Completions schema](https://developers.openai.com/api/reference/cli/resources/chat/subresources/completions)
- [OpenAI Responses schema](https://developers.openai.com/api/reference/ruby/resources/beta/subresources/responses)
- [OpenAI Responses refusal stream events](https://platform.openai.com/docs/api-reference/responses-streaming/response/refusal?lang=python)

No dedicated issue or pull request for Chat to Responses refusal typing was
found. Issue #622 and pull request #623 discuss the adjacent Chat to Anthropic
loss. Pull request #370 covers Anthropic refusal stop metadata. The exact claim
is classified **discussed upstream without a dedicated ticket**.

Result: **PASS**.

## Verdict

- Correctness: **PASS**
- Usefulness: **PASS**
- Upstream status: **PASS**
- Overall: **ACCEPT**
