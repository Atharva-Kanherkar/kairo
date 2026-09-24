# Finding 085: LiteLLM Responses mid-stream fallback tool-call replay: Test Contract

## Functional Behavior

- LiteLLM's Responses-API router fallback wrapper (`Router._aresponses_streaming_iterator`)
  decides whether to replay the original input based only on accumulated text
  (`generated_content`), never on a completed `function_call` item.
- When the primary deployment completes a tool call and then fails with a
  retriable error, the fallback deployment re-runs the same tool and its own
  events are forwarded verbatim, reusing `output_index` from zero.
- A control where the primary fails before any output item is announced
  (`is_pre_first_chunk`) is the correct, conformant fallback path.
- A control where no fallback is triggered at all is conformant.
- The issue writeup names the finding as novel and links the maintainer ruling
  (#40121) that establishes the "one lifecycle" invariant for an adjacent code
  path.

## Unit Tests

- `responses_fallback_preserves_delivered_indexes` reports a violation when an
  `output_index` assigned by one lifecycle is reassigned to an unrelated item
  by a later lifecycle in the same stream, regardless of item type.
- The same checker reports conformant when: only one lifecycle exists; a later
  lifecycle reuses an index nothing was ever assigned to; or a later lifecycle
  reuses an index for the *same* item id.
- The checker fails closed on empty, malformed, and missing-field streams.
- `transcripts/085/test_reproduce.py` unit-tests the deterministic upstream's
  six scenarios and the reproduction rig's per-mode validation logic without
  starting any real process.

## Integration / Functional Tests

- `litellm_responses_fallback_replays_delivered_tool_call` reads the captured
  `trigger-duplicate-tool` evidence for both tested versions and requires 2
  completed tool calls and a checker violation in every trial.
- `litellm_responses_fallback_crashes_official_sdk` reads both SDK-crash
  triggers (`item-type`, `partial-text`) for both versions and requires the
  real `openai` SDK to have raised `AssertionError` with no final response in
  every trial, plus a checker violation.
- `litellm_responses_fallback_controls_preserve_one_output_namespace` reads
  both controls for both versions and requires a checker-conformant verdict
  with the expected upstream-call and tool-call counts in every trial.
- `issue_085_checks_later_trials_and_rejects_malformed_evidence` mutates the
  fifth trial and appends a malformed JSONL line to prove the suite cannot
  pass by only checking the first record.
- Reproduction rate is 5/5 for all three triggers and both controls, on both
  LiteLLM 1.102.1 and current main.

## Smoke Tests

- `cargo test --workspace` passes.
- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes.

## E2E Tests

- The reproduction runs the real LiteLLM proxy CLI as a subprocess bound to a
  loopback port, with a real `router_settings.fallbacks` configuration, driven
  by the real `openai` Python SDK's `client.responses.stream()` accumulator in
  a subprocess. A loopback relay captures the exact bytes the SDK sends and
  receives; a deterministic capture server plays the role of the two provider
  deployments and captures the exact bytes LiteLLM forwards and receives.
- The SDK consumer's own reported outcome (event types, response ids,
  completed tool-call ids, final response, exception type and source line) is
  captured per trial and asserted against in the conformance suite, not just
  the raw wire bytes.

## Manual / cURL Tests

- A reviewer can recreate the pinned LiteLLM 1.102.1 environment (or a fresh
  checkout of current main) and run `transcripts/085/reproduce.py` exactly as
  documented in `issues/085-litellm-responses-fallback-replays-tool-call/README.md`.
- `reproduce.py` refuses to run against an existing output directory, so no
  capture location is implied and a rerun never overwrites committed evidence.
- Verify the raw client-visible SSE body for `trigger-duplicate-tool` contains
  two `response.created` events and two completed `function_call` items with
  different `call_id`s at `output_index` 0; verify `control-pre-first-chunk`'s
  body contains two `response.created` events but only one completed item.
