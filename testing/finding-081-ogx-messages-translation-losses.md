# finding/081-ogx-messages-translation-losses Test Contract

## Functional Behavior

- PR 42 contains only Finding 081. Finding 080 files and harness coverage are absent from the base-to-head diff.
- Finding 081 claims only that OGX v1.4.0 accepts Anthropic `thinking: {"type":"adaptive"}` on `POST /v1/messages`, returns HTTP 200, and forwards no thinking or reasoning configuration to an OpenAI-compatible upstream.
- The deterministic reproduction starts its capture upstream before starting OGX, runs the real OGX v1.4.0 public endpoint, and reports an exact N/N result.
- The trigger-removed or changed control uses the same route and meaningful prompt, with `thinking.type` changed to `enabled`, and records OGX's fail-closed HTTP 400 behavior.
- A live direct-Anthropic control uses `ANTHROPIC_API_KEY` without printing or persisting it, sends only a synthetic prompt, records sanitized raw request and response evidence, and reports structural N/N results.
- The writeup accurately reflects upstream PR 5938, the current OGX source and tests, and does not claim unrelated behavior is novel.

## Unit Tests

- The adaptive-thinking checker rejects evidence where a thinking or reasoning configuration reaches the forwarded OpenAI request.
- The checker accepts a conformant synthetic control where the forwarded request carries an explicit reasoning configuration or the gateway rejects unsupported thinking.
- JSON and JSONL evidence parses successfully and contains the claimed number of trials.

## Integration / Functional Tests

- Run the self-contained deterministic reproduction against OGX v1.4.0 commit `051a8a0` and compare structural results with the frozen fixtures.
- Run the live Anthropic control with the environment-provided `ANTHROPIC_API_KEY`; do not expose the credential in command output, files, logs, or commits.
- Run `cargo test --workspace`.

## Smoke Tests

- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes.
- `git diff --check origin/main...HEAD` has no unintended whitespace errors.

## E2E Tests

- A synthetic direct-Anthropic request with adaptive thinking reaches the public provider endpoint and returns a structurally valid response.
- The same meaningful prompt through OGX's Anthropic endpoint reaches the deterministic OpenAI capture upstream without a thinking or reasoning field.

## Manual / cURL Tests

- Confirm `ANTHROPIC_API_KEY` is set using a boolean presence check only.
- Inspect the frozen direct-provider request and response bytes for inline redaction markers and absence of credentials.
- Confirm `git diff --name-only origin/main...HEAD` contains no `issues/080`, `transcripts/080`, or Finding 080-specific checker/test changes.
