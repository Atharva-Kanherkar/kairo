# Finding 085: LiteLLM Responses mid-stream fallback tool-call replay: Test Contract

## Functional Behavior

- LiteLLM's Responses-API fallback wrapper (`Router._aresponses_streaming_iterator`) decides whether to replay the original input from accumulated text alone (`generated_content`). A completed `function_call` or `reasoning` item is never counted.
- When the primary completes a tool call and then sends a retriable in-band failure (`error`, or `response.failed` with `server_error`), the fallback runs the same request again. Its events are forwarded as a second lifecycle, reusing `output_index` from zero.
- A failure before any output item (`control-fault-before-output`) and a completed primary (`control-no-fault`) are conformant.
- Two boundaries mark the edges of the claim:
  - An announced but never completed item reuses the index without any consumer-visible failure.
  - A transport drop does not reach the fallback.
- The writeup cites #34627 as the governing maintainer ruling (the chat path re-raises once any content streamed), and classifies the finding as `novel` and `bug`.

## Unit Tests

- `responses_no_restart_after_output`:
  - Rejects a `response.created` that follows any announced output item, whether or not the fallback's index is renumbered.
  - Accepts a second `response.created` before any output item.
  - Fails closed on empty or non-JSON streams.
- `responses_fallback_not_spliced_after_delivery`:
  - Rejects any item from a later upstream attempt in the client stream after a failed attempt's item was delivered. This covers the "one clean lifecycle with renumbered indexes" variant.
  - Accepts no failure, a failure before any delivered item, and a failed attempt whose items never reached the client.
- `responses_fallback_preserves_delivered_indexes`: unchanged from the first revision. It rejects one `output_index` reassigned to a different item.
- `transcripts/085/test_reproduce.py` covers:
  - every deterministic upstream scenario, including the transport drop and the reasoning-first fallback
  - the per-mode, per-SDK-version validation
  - the token-boundary credential check
- `transcripts/085/codex/test_run_codex_matrix.py` covers:
  - byte-exact externalization and restore, including tamper detection and only-top-level keys
  - sanitization
  - the Codex upstream scenarios
  - the credential check

## Integration / Functional Tests

- `litellm_responses_fallback_replays_delivered_tool_call`: both duplicate modes, on both versions. Requires, in every trial:
  - two upstream calls
  - two completed calls under every SDK version
  - all three checkers reporting a violation
- `litellm_responses_fallback_crashes_openai_python_before_3_14`: both crash modes. Requires `AssertionError` with no final response under openai 2.54.0 and 3.13.0, and a clean final response under 3.14.0 and 3.19.2.
- `litellm_responses_fallback_controls_and_boundaries`:
  - The controls are conformant under all three checkers.
  - The announced-only boundary violates the checkers but every SDK completes one call.
  - The transport drop makes one upstream call, is conformant, and surfaces an error.
- `issue_085_checks_later_trials_and_rejects_malformed_evidence`: a mutated fifth trial, a fifth trial with its SDK replays removed, and an appended malformed line each fail.
- `issue_085_splice_invariant_survives_renumbering`: a real trigger capture, rewritten to one lifecycle with shifted indexes, passes the index and restart checkers and still fails the splice checker.
- `codex_runs_the_side_effect_twice_after_a_litellm_fallback`: every Codex run, on 1.102.1, on main, and with the live fallback. For each run it checks:
  - the side-effect count, from the ledger and from Codex's own events
  - Codex's returned tool results
  - the checker verdicts on the first turn
  - that each shared reference file exists

## Smoke Tests

- `cargo test --workspace`, `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`, and `python3 tools/update-readme-counts.py --check` pass.
- `python3 transcripts/085/codex/verify_refs.py ...` rebuilds all 160 committed Codex request bodies byte for byte.

## E2E Tests

- `transcripts/085/reproduce.py` runs:
  - the real LiteLLM proxy CLI with a real `router_settings.fallbacks` configuration
  - driven by the real `openai` SDK's `client.responses.stream()`
  - with each captured public body replayed to openai 3.13.0, 3.14.0, and 3.19.2
- `transcripts/085/codex/run_codex_matrix.py` runs the real Codex CLI 0.156.1 through the same proxy. It uses hermetic `HOME` and `CODEX_HOME`, disables Codex's own retries, and measures the side effect as lines in `ledger.txt`.

## Manual / cURL Tests

- A reviewer can recreate the pinned environments and rerun both runners exactly as documented in the issue writeup and `transcripts/085/codex/README.md`.
- Both runners refuse an existing output directory, so a rerun never overwrites committed evidence.
