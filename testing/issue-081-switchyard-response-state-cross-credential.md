# Issue 081 Switchyard Response State Cross Credential - Test Contract

## Functional Behavior

- Run Switchyard commit `bfcd023cb6791efd8ac59d32afb5095c74fef1ff` through its real `switchyard-server` HTTP entry point.
- Configure a documented OpenAI Chat backend with `forward_auth = true` and call it through `POST /v1/responses`.
- Two distinct caller bearer credentials that receive the same backend response ID must not share locally materialized continuation history.
- The failing sequence is attacker seed, victim seed with a canary, then attacker continuation by the colliding response ID.
- The control changes only the backend response IDs so they are distinct per credential scope.
- Synthetic credentials and canary prompts only. No real credential or private prompt may appear in evidence.

## Unit Tests

- Add a harness checker that reports a violation when a forwarded request authenticated as caller A contains caller B's canary history.
- The checker must accept the unique-ID control and reject malformed or incomplete evidence.

## Integration / Functional Tests

- Reproduce the collision 5 of 5 times with the real release server and deterministic capture upstream.
- Run the unique-ID control 5 of 5 times with the same server, route, requests, and credentials.
- Save sanitized client requests, client responses, and forwarded upstream requests under `transcripts/081/`.

## Smoke Tests

- `cargo test --workspace`
- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets -- -D warnings`
- `python3 tools/update-readme-counts.py --check`

## E2E Tests

- The credential-free reproduction script starts the mock upstream and pinned Switchyard binary, performs both 5-trial matrices, and exits nonzero unless the bug and control both match the frozen expectations.

## Manual / cURL Tests

- The issue README gives clean-checkout build and reproduction commands.
- A reviewer can inspect the attacker continuation response and the matching forwarded request to confirm the victim canary crossed into the attacker credential scope.
