# Finding 084: LiteLLM OpenAI pass-through provider ID: Test Contract

## Functional Behavior

- LiteLLM Proxy 1.102.1 receives a valid JSON request to `POST /openai_passthrough/v1/alpha/search` containing a provider-owned top-level `id`.
- The pass-through request forwarded upstream retains the same top-level `id` value.
- The captured violating request without `id` must fail the invariant checker; a direct control retaining the `id` must pass.
- The issue writeup names the finding as duplicate-open and links the current upstream issue and fix PR.

## Unit Tests

- `provider_request_id_preserved` reports a violation when the client JSON has a top-level `id` absent from forwarded JSON.
- The same checker reports conformant when the forwarded JSON retains the exact ID and when the client body has no ID.

## Integration / Functional Tests

- `litellm_alpha_search_id_dropped_violation` reads the captured client and forwarded request bodies and detects the missing ID.
- `litellm_alpha_search_id_direct_control_conformant` reads the direct control and confirms the ID is retained.
- Reproduction rate remains 5/5 through LiteLLM and 5/5 direct controls against the deterministic capture upstream.

## Smoke Tests

- `cargo test --workspace` passes.
- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes.

## E2E Tests

- The real Codex CLI 0.156.1 runs through LiteLLM 1.102.1 to the live OpenAI API in five setups, each with default config and with `web_search="live"`. See `transcripts/084/codex/README.md`.
- `codex_consumer_search_calls_lose_provider_request_id` reads all 24 captured Codex search calls through `/openai_passthrough` and requires every pair to violate the invariant.
- The no-gateway control keeps `id`, gets HTTP 200, and Codex answers the question.

## Manual / cURL Tests

- Reviewer can recreate the pinned LiteLLM 1.102.1 environment and run the documented capture server, proxy CLI, and replay commands from `issues/084-litellm-openai-passthrough-drops-id/README.md`.
- Verify the raw client request contains `id`, the direct upstream request retains it, and each forwarded proxy request omits it.
- `capture_upstream.py` and `replay.py` refuse to run without `--output-dir`, so no capture location is implied.
- The documented reviewer run writes every capture to a temporary directory outside the repository, and `git status --porcelain` is empty afterwards.
- The committed snapshot under `transcripts/084/raw/replay/` changes only when a maintainer passes that directory explicitly to both scripts.
