# 072 Bifrost Anthropic tool_choice 'any' leak: Test Contract

## Functional Behavior

- When a client sends an Anthropic `/v1/messages` request with
  `"tool_choice": {"type": "any"}` to Bifrost, and Bifrost routes to an OpenAI
  backend (`/v1/responses` or `/v1/chat/completions`), Bifrost must translate
  the forced tool choice to `"tool_choice": "required"`.
- Bifrost v2.0.0 currently leaks `"tool_choice": "any"` verbatim in both
  `/v1/responses` and `/v1/chat/completions` upstream payloads. Because `"any"`
  is not a valid OpenAI tool_choice value, OpenAI backends reject the request
  with HTTP 400 Bad Request.
- OpenAI Responses control: A client sending `/v1/responses` with
  `"tool_choice": "required"` through Bifrost preserves `"tool_choice": "required"`
  upstream.
- OpenAI Chat control: A client sending `/v1/chat/completions` with
  `"tool_choice": "required"` through Bifrost preserves `"tool_choice": "required"`
  upstream.
- Anthropic auto control: A client sending Anthropic `/v1/messages` with
  `"tool_choice": {"type": "auto"}` translates to `"tool_choice": "auto"`
  upstream.

## Unit Tests (Rust, `crates/harness/src/checks.rs`)

- `anthropic_tool_choice_any_mapped_to_required` verifies that upstream
  forwarded requests contain `"tool_choice": "required"`.
- The checker rejects payloads where `tool_choice` is `"any"`, missing, or
  unexpected.
- Unit test `anthropic_tool_choice_any_checker` validates correct handling of
  both conformant and violating JSON payloads.

## Integration / Conformance Tests (Rust, `crates/harness/tests/conformance.rs`)

- `bifrost_leaks_anthropic_tool_choice_any` confirms that the recorded
  transcripts from Bifrost v2.0.0 exhibit the `"tool_choice": "any"` violation.
- `bifrost_openai_responses_keeps_tool_choice_required` confirms that the
  OpenAI Responses control preserves `"tool_choice": "required"`.
- `bifrost_openai_chat_keeps_tool_choice_required` confirms that the
  OpenAI Chat control preserves `"tool_choice": "required"`.
- `cargo test --workspace` passes all tests.

## Smoke Tests

- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes.
- `python3 transcripts/072/test_reproduce.py` passes under both normal and `-O`.
- `python3 tools/test_update_readme_counts.py` passes.

## E2E Tests

- Run pinned Bifrost v2.0.0 binary (`bifrost-http-0`) with configuration
  `transcripts/072/bifrost.json` against mock upstream on port 9912.
- Execute 5/5 trials for the violation and each control using
  `transcripts/072/reproduce.py`.
- Verify all JSONL records are saved with status, request, response, and forwarded
  request bytes.
- Confirm zero credentials or private keys are recorded.
