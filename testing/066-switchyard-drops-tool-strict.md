# 066-switchyard-drops-tool-strict - Test Contract

## Functional Behavior

- An Anthropic `/v1/messages` request whose named tool has `strict: true` must
  forward `function.strict: true` when Switchyard targets OpenAI Chat.
- The recorded Switchyard main capture must be reported as a violation because
  all five Anthropic-ingress requests omit that nested field while clients
  receive HTTP 200.
- The same Switchyard process must report conformance for an OpenAI Chat
  ingress control that forwards `function.strict: true` five times.
- The evidence contains no credentials and the trigger is limited to one
  client-supplied function-tool field.

## Unit Tests

- The existing `tool_strict_forwarded` checker reports `Violation` for each of
  the five Anthropic-ingress capture records.
- The same checker reports `Conformant` for each of the five OpenAI Chat
  ingress control records.

## Integration Tests

- `switchyard_drops_anthropic_tool_strictness` validates five captured
  Anthropic to OpenAI Chat translation runs and their HTTP 200 client results.
- `switchyard_openai_route_keeps_tool_strictness` validates the five-run
  same-process OpenAI Chat control.

## Smoke Tests

- `cargo test --test conformance switchyard_tool_strictness` passes.
- `cargo test` passes.
- `python3 tools/update-readme-counts.py --check` reports current counters.

## E2E Tests

N/A. This contribution freezes a deterministic keyless local capture rather
than exercising a provider endpoint.

## Manual Tests

- Build Switchyard main with Rust 1.96.1 and run it against a local
  OpenAI Chat capture server.
- POST the Anthropic request in `transcripts/066/repro.py` five times and
  verify every captured `tools[0].function` omits `strict` despite HTTP 200.
- POST the matching OpenAI Chat request five times and verify every captured
  `tools[0].function.strict` is `true`.
