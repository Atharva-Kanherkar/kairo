# 074 LiteLLM MCP Responses stream collision, test contract

## Functional Behavior

- A documented streaming `POST /v1/responses` request with one proxy MCP tool and
  `require_approval: "never"` executes the tool and starts a follow-up model round.
- The client-visible stream must remain one valid Responses lifecycle: one
  `response.created`, one terminal `response.completed`, one public response id,
  and output indexes that never identify unrelated items.
- LiteLLM 1.99.0 and 1.100.0 must reproduce the violation five times each through
  the real proxy route and a real local MCP server.
- Changing only `require_approval` to `"always"` must prevent auto-execution and
  produce one SDK-consumable lifecycle five times on each version.
- A no-tool request must also produce one SDK-consumable lifecycle.
- The official OpenAI Python SDK 2.54.0 must demonstrate the consumer consequence:
  the trigger raises `AssertionError` before returning the final answer, while
  both controls complete.

## Unit Tests

- The Responses lifecycle checker accepts one created/completed pair with stable
  identity and non-colliding item indexes.
- It rejects multiple created or completed events, changing response ids,
  colliding output indexes, malformed SSE, and missing terminal events.

## Integration / Functional Tests

- A conformance test reads every retained trigger and control record, verifies raw
  wire fields and trial provenance, and applies the lifecycle checker.
- Mutation tests prove that later trials are inspected and that a parse failure
  cannot satisfy the finding.
- The reproducer's Python tests validate its deterministic upstream, capture
  sanitization, port checks, and result classifier.

## Smoke Tests

- `python3 -B transcripts/074/reproduce.py --help` exits successfully.
- `python3 -B -m unittest transcripts/074/test_reproduce.py` passes with and
  without Python optimization.

## E2E Tests

- Run the reproducer into a fresh temporary directory against LiteLLM 1.100.0.
- Confirm five trigger SDK failures, five approval-control successes, and five
  no-tool-control successes.
- The committed 1.99.0 and 1.100.0 captures must pass the offline conformance suite.

## Manual / cURL Tests

- The issue writeup must include exact environment setup and reproduction commands.
- Inspect a trigger capture and confirm two created/completed lifecycles, distinct
  response ids, reused output index zero, and mismatched MCP call item ids.
- Inspect the approval control and confirm one created/completed lifecycle and a
  successful SDK final response.

## Repository Checks

- `cargo test --workspace`
- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets -- -D warnings`
- `python3 tools/update-readme-counts.py --check`
- Independent read-only review with
  `.github/agents/kairo-reproduction-reviewer.agent.md`
