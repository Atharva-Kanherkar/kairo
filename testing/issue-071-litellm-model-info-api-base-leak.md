# issue/071-litellm-model-info-api-base-leak Test Contract

Supersedes the prior contract to close all seven findings from PR #27 review
comment (https://github.com/Atharva-Kanherkar/kairo/pull/27#issuecomment-5557668101).

## Functional Behavior

- Capture raw sanitized HTTP request and response bytes for `GET /model/info`,
  `GET /v1/model/info`, and their controls, using the literal bytes sent and
  received on the wire (no reconstruction from parsed headers/body).
- Reproduce the `api_base` query credential exposure 5/5 on LiteLLM 1.99.0.
- Report only the unauthenticated default-local access boundary that the
  reproduction demonstrates; do not claim authenticated non-admin access.
- Classify `api_base` exposure and custom-header exposure independently
  against current upstream evidence.
- Keep issue 071 changes isolated from unrelated issue 070 bookkeeping.
- Make the reproduction runner write reviewer captures to an ignored or
  caller-selected directory by default.
- Fail fast if the reproduction port is already bound by another process,
  rather than silently probing whatever is listening there.
- Detect and report the spawned LiteLLM process exiting during startup
  instead of waiting out the full readiness timeout.
- Produce actionable, credential-free diagnostics for a missing LiteLLM
  executable and for a startup timeout.
- Validate every HTTP status code and all 5/5 determinism counters with
  explicit `if`/`raise` checks (never bare `assert`, so behavior is identical
  under `python3 -O`) before any fixture file is written.
- A failed probe or determinism check must leave all existing fixtures
  byte-for-byte unchanged. Stage file contents before replacement; replacement
  is atomic per file, not across the entire batch if a filesystem error occurs.
- Record route identity (`request_path`) and the observed HTTP status
  alongside every captured body, so a swapped-route capture or a 404 cannot
  be mistaken for a genuine 200 model-info response. Cross-check that metadata
  and body against the actual request line, response status, and body in `.http`.
- Check the installed LiteLLM version before launching and run from a temporary
  working directory with an allowlisted environment, without loading repo `.env`.
- Reject a model-info response whose `data` array is empty as a failure to
  reproduce, not a vacuous pass.

## Unit Tests (Rust, `crates/harness/src/checks.rs`)

- `model_info_omits_api_base_secret_flags_leaked_key` reports a violation for
  a leaked canary.
- The checker reports a violation when a model-info response lacks the
  expected response structure.
- The checker reports a violation when a model-info response's `data` array
  is empty (nothing to check is not conformant).
- The checker reports conformance for a structurally valid model-info
  response without `api_base` credentials.
- `model_info_capture_identity` (or equivalent) reports a violation when the
  captured `request_path` does not match the expected route, and when the
  captured status is not 200.
- `model_info_omits_api_base_secret`'s doc comment describes only the caller-supplied
  marker check it performs, not a claim about which secret types it knows.

## Integration / Functional Tests (Rust, `crates/harness/tests/conformance.rs`)

- `cargo test --workspace` passes.
- `/model/info` and `/v1/model/info` fixtures are checked for route identity
  and status BEFORE the body is checked for a leaked canary.
- A swapped-route or non-200 fixture fails the route-identity check even
  when the body is byte-identical to a genuine capture.
- The `/v1/models` whole-body control uses `response_omits_secret` against
  the full response body, with a negative-coverage case proving a canary
  placed outside `litellm_params.api_base` (where
  `model_info_omits_api_base_secret` would not look) is still caught.
- `/health/liveliness` has its own captured JSON fixture and is checked with
  `response_omits_secret` as a whole-body control.

## Smoke Tests

- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes.
- `python3 -m unittest discover -s transcripts/071 -p 'test*.py'` passes.
- The same discovery run passes under `python3 -O`.

## E2E Tests

- Start LiteLLM 1.99.0 through its real CLI with the issue 071 configuration.
- Run five unauthenticated probes against each model-info route.
- Run `/v1/models` and `/health/liveliness` controls with the same process
  and configuration.
- Occupied-port path: binding the reproduction port ahead of time causes an
  immediate, actionable failure instead of a silent capture against the
  wrong listener.
- Early-exit path: a LiteLLM process that exits during startup is detected
  and reported without waiting out the readiness timeout.
- Partial-failure path: an induced non-200 response or 4/5 determinism result
  leaves a pre-existing output directory's evidence byte-for-byte unchanged.
- The HTTP capture test sends an actual loopback request and compares the saved
  request and response bytes with what the test server received and sent.

## Documentation / Count-Guard Tests

- README.md's Status table, its prose folder count, and
  `issues/SCOREBOARD.md`'s coverage line all agree with the number of unique
  git-tracked `issues/NNN-*` writeups.
- `tools/update-readme-counts.py --check` fails when any of the three
  disagree, and a regression test (`tools/test_update_readme_counts.py`)
  exercises that failure path directly rather than relying only on manual
  inspection.
- Remove unmaintained aggregate defect totals instead of treating folder counts
  as defect counts; the ledger retains individual findings and their versions.

## Manual / cURL Tests

- Run the issue 071 reproduction with a temporary output directory and
  inspect the raw `.http` captures for literal CRLF wire framing.
- Confirm captured files contain canaries only and contain no credential
  sourced from `.env`.
- Confirm the PR diff contains no unrelated issue entry or count change.
