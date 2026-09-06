# 072 Bifrost Anthropic tool_choice 'any' leak: Test Contract

## Functional Behavior

- When a client sends an Anthropic `/anthropic/v1/messages` request with
  `"tool_choice": {"type": "any"}` to Bifrost, and Bifrost routes to an OpenAI
  backend (`/v1/responses` or `/v1/chat/completions`), Bifrost must translate
  the forced tool choice to `"tool_choice": "required"`.
- Test the official `transports/v2.0.0` release at
  `e4a30d6041c0446603aea615bc5da340dac001b1`, core v1.8.3. Check embedded
  provenance before running; reject a different or modified binary.
- Use `gpt-4o` consistently. Exercise built-in OpenAI Responses and a custom
  OpenAI provider with only Chat operations enabled. Persist both configurations.
- Measure whether the real OpenAI endpoint rejects the forwarded `"any"` with
  HTTP 400. Do not replace live evidence with a schema-rejecting mock.
- OpenAI Responses control: A client sending `/v1/responses` with
  `"tool_choice": "required"` through Bifrost preserves `"tool_choice": "required"`
  upstream.
- OpenAI Chat control: A client sending `/v1/chat/completions` with
  `"tool_choice": "required"` through Bifrost preserves `"tool_choice": "required"`
  upstream.
- Anthropic auto control: A client sending Anthropic `/anthropic/v1/messages` with
  `"tool_choice": {"type": "auto"}` translates to `"tool_choice": "auto"`
  upstream.

## Unit Tests (Rust, `crates/harness/src/checks.rs`)

- `anthropic_tool_choice_any_mapped_to_required` verifies that upstream
  forwarded requests contain `"tool_choice": "required"`.
- The checker and sweep probe accept only the promised bare `"required"` string.
  Reject `"any"`, `"auto"`, missing, null, non-string values and malformed
  objects including mode-only, type-only and conflicting type/mode objects.
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
- Check all five records in each fixture, their path, original input, raw body
  consistency, statuses and consumer result. A bad later trial must not be hidden
  by a conformant first record. Check both Chat and Responses violation paths.
- Python tests cover binary provenance, all-record validation, malformed capture
  rejection, secret redaction, and the sweep probe's malformed-object cases.

## Smoke Tests

- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes.
- `python3 transcripts/072/test_reproduce.py` passes under both normal and `-O`.
- `python3 tools/test_update_readme_counts.py` passes.

## E2E Tests

- Run pinned Bifrost v2.0.0 binary (`bifrost-http-0`) with the recorded
  `transcripts/072/local/{responses,chat}-config.json` configurations. The runner
  regenerates these configurations for its chosen loopback upstream port.
- Execute 5/5 trials for the violation and each control using
  `transcripts/072/reproduce.py`.
- Verify all JSONL records are saved with status, request, response, and forwarded
  request bytes.
- Confirm zero credentials or private keys are recorded.
- Live mode loads `OPENAI_API_KEY` from the environment or `.env` with a dotenv
  parser, never shell evaluation. Keep the key only in memory in a loopback relay;
  Bifrost receives a fake key. Relay unmodified request bytes only to
  `https://api.openai.com`, without following redirects or logging headers.
- Preserve every trial's actual request, forwarded body, upstream response and
  client response as raw UTF-8 bodies in JSONL, plus statuses and correlation.
  Strip credential/account headers and fail closed if secrets occur in bodies.
- Use an actual pinned Anthropic SDK client with retries disabled. Show the
  rejected forced-tool request cannot dispatch a tool. With one available tool,
  naming that same tool is the one-trigger control and must dispatch it once.
- Include same-dialect `required` controls and direct-provider controls using the
  captured body with only `tool_choice` changed to `required`.
- Live tests are opt-in, bounded to five runs per condition with 100 output tokens.
  Report measured N/N results without generalizing to all agents or deployments.

## Manual Commands

```sh
python3 transcripts/072/reproduce.py --output-dir /path/to/new/local-captures
python3 transcripts/072/reproduce.py --live --env-file .env --output-dir /path/to/new/live-captures
```

An independent read-only reviewer must rerun both paths and repository checks.
Until that review passes, do not mark the repaired PR independently approved.
