# Issue 083 Switchyard Conversation Continuation - Test Contract

## Functional Behavior

- Run Switchyard commit `bfcd023cb6791efd8ac59d32afb5095c74fef1ff` through its real `switchyard-server` HTTP entry point.
- Configure a documented OpenAI Chat backend and call it through `POST /v1/responses`.
- A second request carrying the same Responses `conversation` ID must include the first turn when Switchyard materializes history for the Chat backend.
- The failing sequence is a seed request with a conversation ID followed by a recall request with the same conversation ID.
- The control changes only the continuation mechanism to the seed response's `previous_response_id`.
- Synthetic conversation content only. No credential or private prompt may appear in evidence.

## Unit Tests

- Add a harness checker that reports a violation when a forwarded continuation omits the seed turn.
- The checker must accept the `previous_response_id` control and reject malformed or incomplete evidence.

## Integration / Functional Tests

- Reproduce the collision 5 of 5 times with the real release server and deterministic capture upstream.
- Run the `previous_response_id` control 5 of 5 times with the same server, route, and requests.
- Save sanitized client requests, client responses, and forwarded upstream requests under `transcripts/083/`.

## Smoke Tests

- `cargo test --workspace`
- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets -- -D warnings`
- `python3 tools/update-readme-counts.py --check`

## E2E Tests

- The credential-free reproduction script starts the mock upstream and pinned Switchyard binary, performs both 5-trial matrices, and exits nonzero unless the bug and control both match the frozen expectations.

## Manual / cURL Tests

- The issue README gives clean-checkout build and reproduction commands.
- A reviewer can inspect the conversation continuation response and matching forwarded request to confirm the seed canary disappeared, while the response-ID control retains it.
