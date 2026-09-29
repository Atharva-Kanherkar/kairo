# Finding 090: OpenShell allow_uninspected_credentials merge-scope inheritance: Test Contract

## Functional Behavior

- Exercise OpenShell through the real public path: the released `openshell` CLI talking to a locally run gateway whose server-side policy merge applies an `AddRule` update.
- Seed one rule on a credentialed WebSocket endpoint with two binaries, A (`/usr/local/bin/tool-a`) and B (`/usr/local/bin/tool-b`), and `allow_uninspected_credentials` off.
- Failing case: an `AddRule` update naming only binary A with `allow_uninspected_credentials` on must succeed, and the effective merged policy must show B, which the update never declared, with the flag on.
- Trigger-removed control: the same update without the flag must leave the effective policy unchanged for B.
- Sibling control: the same update shape with `websocket_credential_rewrite` in place of the flag must be rejected by the inheritance guard with an error asking to also declare `/usr/local/bin/tool-b`.
- Each of the three cases must pass 5 of 5 independent trials on OpenShell v0.1.2 (`6648bd0c290efbc41ba131ee9831ee45cd431f94`) and on a recorded current `main` commit (`a6eefcf` or newer).
- The claim stays narrow: `endpoint_attributes_cover` omits `allow_uninspected_credentials` from its coverage set while `merge_endpoint` widens the flag, so the guard never forces the update onto its own rule. Source lines are cited at both tested commits.
- Runtime consequence, 3 of 3 through the real `openshell-supervisor-network` L7 WebSocket relay: with the flag off a credential-bearing binary frame is closed with code 1008; with the flag on the same frame is forwarded unchanged.
- The unavailable rung is named: a live B process inside a booted sandbox could not run on macOS because `supervisor:dev` `/etc/passwd` extraction returned 404. No evidence from that rung is claimed.
- Evidence includes sanitized raw CLI output, gateway logs, seed and update policies, effective policies, relay captures, configurations, version metadata, commands, and an N-of-N result matrix per version.
- The issue answers all three gates, all five bug-or-not questions, records exactly one label, gives a one-sentence maintainer fix, a final verdict, and an upstream search dated 2026-09-29 with links and terms.

## Unit Tests

- The finding 090 invariant checker accepts the committed v0.1.2 and current-main evidence only when every declared invariant is supported by raw artifacts.
- It rejects a failing trial where B does not end with the flag on, or where the update declared B.
- It rejects a trigger-removed trial that changes B, and a sibling trial that is not rejected by the inheritance guard.
- It rejects a relay result unless flag-off closes with 1008 and flag-on forwards bytes identical to the input frame.
- It rejects missing trials, fewer than 5 trials per case, fewer than 3 relay runs, missing versions, commit mismatches, summaries that contradict raw artifacts, vacuous evidence, and unsanitized credentials or absolute local paths.

## Integration / Functional Tests

- `transcripts/090/reproduce` tooling parses and runs deterministically against the pinned CLI and gateway, writing a fresh output directory without overwriting committed evidence.
- A v0.1.2 run and a current-main run each regenerate all three cases and pass the finding 090 checker.
- Focused Rust conformance tests load both committed matrices and the relay evidence and prove the failing case violates the declared-binary scope invariant while both controls conform.
- Mutation tests prove the checker fails closed on incomplete, contradictory, vacuous, and unsanitized evidence.

## Smoke Tests

- `cargo test --workspace` passes.
- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets --all-features -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes after README.md and issues/SCOREBOARD.md are updated by that tool.
- Staged artifacts contain no credentials, private prompts, absolute local paths, em dashes, en dashes, generated build state, or unrelated changes.
- The primary worktree remains byte-for-byte unchanged.

## E2E Tests

- Start the pinned gateway with the supported local Docker configuration, apply the seed policy with the CLI, apply each update, and read the effective policy back through the CLI for every trial.
- Run the relay flag-off and flag-on cases through the real L7 relay code with the same credential-bearing binary frame.
- Repeat the full matrix on v0.1.2 and current main.
- N/A: live in-sandbox process run, blocked on macOS as stated above.

## Manual / cURL Tests

- Follow the issue writeup's commands for each version to regenerate fresh output directories.
- Run the finding 090 checker against each fresh result set.
- Inspect one failing trial and both controls to confirm the effective policy, CLI output, and matrix agree.
- Use the repository pull request template without deleting any required section.
- Independent read-only review remains a separate gate and must rerun the critical path before any `ACCEPT`.
