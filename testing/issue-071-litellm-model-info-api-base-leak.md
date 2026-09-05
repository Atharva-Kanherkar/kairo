# issue/071-litellm-model-info-api-base-leak Test Contract

## Functional Behavior

- Capture raw sanitized HTTP request and response bytes for `GET /model/info`, `GET /v1/model/info`, and their controls.
- Reproduce the `api_base` query credential exposure 5/5 on LiteLLM 1.99.0.
- Report only the unauthenticated default-local access boundary that the reproduction demonstrates; do not claim authenticated non-admin access.
- Classify `api_base` exposure and custom-header exposure independently against current upstream evidence.
- Keep issue 071 changes isolated from unrelated issue 070 bookkeeping.
- Make the reproduction runner write reviewer captures to an ignored or caller-selected directory by default.

## Unit Tests

- `model_info_omits_api_base_secret_flags_leaked_key` reports a violation for a leaked canary.
- The checker reports a violation when a model-info response lacks the expected response structure.
- The checker reports conformance for a structurally valid model-info response without `api_base` credentials.

## Integration / Functional Tests

- `cargo test --workspace` passes.
- Conformance fixtures exercise violating and conforming model-info responses.
- Sanitized raw HTTP captures match the reproduced LiteLLM behavior.

## Smoke Tests

- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes.

## E2E Tests

- Start LiteLLM 1.99.0 through its real CLI with the issue 071 configuration.
- Run five unauthenticated probes against each model-info route.
- Run `/v1/models` and `/health/liveliness` controls with the same process and configuration.

## Manual / cURL Tests

- Run the issue 071 reproduction with a temporary output directory and inspect the raw `.http` captures.
- Confirm captured files contain canaries only and contain no credential sourced from `.env`.
- Confirm the PR diff contains no unrelated issue entry or count change.
