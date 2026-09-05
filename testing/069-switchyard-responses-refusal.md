# 069 Switchyard Responses refusal semantics: Test Contract

## Functional Behavior

- A valid OpenAI Chat completion whose assistant message contains a non-empty
  `message.refusal` must remain machine-identifiable as a refusal after
  Switchyard translates it to an OpenAI Responses client response.
- A buffered `/v1/responses` result must expose the refusal as a content part
  with `type: "refusal"` and the original refusal string, not as ordinary
  `output_text` and not as an empty successful turn.
- A streamed `/v1/responses` result must emit `response.refusal.delta` and
  `response.refusal.done` for the original refusal string, not only
  `response.output_text.*` events.
- The same upstream refusal sent through Switchyard's same-dialect
  `/v1/chat/completions` route must preserve `message.refusal` as the control.
- Evidence must cover current Switchyard `main` and pull request #623's pinned
  head so the report distinguishes the existing erasure from the semantic loss
  that remains after the proposed refusal-text fix.

## Unit Tests

- `responses_refusal_semantics_preserved` rejects a buffered Responses payload
  with no typed refusal content.
- `responses_refusal_semantics_preserved` rejects a Responses SSE stream with no
  refusal events.
- The checker accepts conformant buffered and streamed Responses fixtures and
  rejects malformed or vacuous upstream evidence.

## Integration / Functional Tests

- Build and start the real `switchyard-server` at pinned current `main` and
  drive its public `/v1/responses` and `/v1/chat/completions` endpoints against
  a deterministic local OpenAI Chat capture upstream.
- Repeat buffered violation, streamed violation, and same-dialect control five
  times per pinned revision and save sanitized raw request, upstream response,
  and client response bytes under `transcripts/069/`.
- Demonstrate at the consumer boundary that a detector keyed to the documented
  Responses refusal content type and stream events records a false negative.

## Smoke Tests

- The reproduction script exits successfully only after all expected trial
  counts and wire shapes are observed.
- `cargo test --workspace` passes with the new transcript replay coverage.
- Formatting, clippy, and README count checks pass.

## E2E Tests

- The real Switchyard HTTP server receives an OpenAI Responses request,
  contacts the deterministic Chat Completions backend, and returns the
  translated client response. No provider credential is required because the
  claim concerns gateway translation of captured provider bytes.

## Manual / cURL Tests

- Follow the exact build and reproduction commands documented in
  `issues/069-switchyard-responses-refusal/README.md` from a clean checkout.
- Inspect each JSONL record and confirm the upstream refusal text and type,
  translated Responses result, route, status, and consumer classification.
- Run the same-dialect Chat route control with the same prompt, model, backend,
  and upstream response.
