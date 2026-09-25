# Finding 087: LiteLLM MCP retry re-executes a tool: Test Contract

## Functional Behavior

- Exercise LiteLLM through the real public proxy entry point, `POST /v1/chat/completions`, with an MCP server configured through `mcp_servers`.
- The deterministic upstream first requests the side-effecting MCP tool and then returns a retryable HTTP 500 after receiving the tool result.
- With LiteLLM router retries enabled, the same MCP tool must be observed executing three times: the initial attempt plus two retries.
- With `num_retries: 0`, the same faulting request must execute the MCP tool exactly once.
- With the fault disabled, the same request and retry configuration must execute the MCP tool exactly once and complete successfully.
- Each cell must pass 5 of 5 independent trials on LiteLLM 1.102.1 and on the tested current `main` commit.
- Evidence must include sanitized raw client request, client response, forwarded upstream requests and responses, MCP tool ledger entries, configurations, version metadata, and an N-of-N result matrix.
- The issue must answer all five bug-or-not questions, name the affected workflow and measured consequence, identify the smallest trigger, and record an upstream-status search dated 2026-09-25.

## Unit Tests

- The finding 087 invariant checker rejects evidence when a retry-enabled violation run does not execute the tool three times.
- The checker rejects evidence when either control executes the tool more than once.
- The checker rejects incomplete matrices, missing trials, inconsistent summaries, malformed records, vacuous evidence, missing raw artifacts, and unsanitized credentials or local paths.
- The checker accepts the committed pinned-release and current-main matrices only when every declared invariant is supported by the raw artifacts.

## Integration / Functional Tests

- The repaired `transcripts/087/reproduce.py` parses with `py_compile` alongside `mock_upstream.py` and `mcp_server.py`.
- A canonical LiteLLM 1.102.1 run regenerates all four cells and passes the finding 087 checker.
- A run from the current LiteLLM `main` checkout regenerates all four cells and passes the same checker.
- Focused Rust conformance tests load both committed matrices and prove that the retry-enabled cells violate the exactly-once tool-execution invariant while both controls conform.
- Mutation tests prove the checker fails closed for vacuous, incomplete, and internally inconsistent evidence.

## Smoke Tests

- `cargo test --workspace` passes.
- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets --all-features -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes.
- Focused finding 087 checks pass.
- Staged artifacts contain no credentials, private prompts, absolute local paths, em dashes, or en dashes.
- The unrelated primary worktree remains unchanged.

## E2E Tests

- Start the real LiteLLM proxy CLI with the generated configuration, the deterministic OpenAI-compatible upstream, and the real MCP server.
- Send the client request with the real OpenAI Python SDK and `max_retries=0`, so only LiteLLM retry behavior can cause repeated execution.
- For every trial, reconcile the raw upstream exchanges and MCP ledger with the per-run summary before accepting the matrix.
- Repeat the complete matrix on LiteLLM 1.102.1 and current LiteLLM `main`.

## Manual / cURL Tests

- Follow the issue writeup's pinned environment and current-main commands to generate fresh output directories without overwriting committed evidence.
- Run the finding 087 checker against each fresh `results.json` and its referenced raw artifacts.
- Inspect one retry-enabled trial and both controls to confirm that request bodies, response bodies, and ledger counts agree with the matrix.
- Use the repository pull request template without deleting any required section.
- Independent review remains a separate, read-only gate and must rerun the critical path before any `ACCEPT` decision.
