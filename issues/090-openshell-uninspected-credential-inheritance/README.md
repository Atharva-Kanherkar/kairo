# 090, OpenShell policy updates extend an uninspected-credential exception to an undeclared binary

- **Upstream**: [NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell). The authorization-inheritance guard was established by [PR #2499](https://github.com/NVIDIA/OpenShell/pull/2499); [PR #2493](https://github.com/NVIDIA/OpenShell/pull/2493) later added `allow_uninspected_credentials`. The combination leaves this flag outside the existing guard. No dedicated report for this path was found on 2026-09-30. Related [issue #2497](https://github.com/NVIDIA/OpenShell/issues/2497) covers the general implicit-widening invariant.
- **Tool under test**: OpenShell v0.1.2 release `6648bd0c290efbc41ba131ee9831ee45cd431f94`, plus upstream `main` `c0eb3dbd30b50e7e24666af857b8320e9d83a6cc` (`0.1.3-dev.22+gc0eb3dbd3`); local CLI and gateway, Docker-backed Ready sandbox, macOS arm64 host and Linux aarch64 containers.
- **Reproduced**: 2026-09-30. Public `openshell policy update` path, gateway policy merge, sandbox binary `/usr/local/bin/tool-b`, credentialed WebSocket endpoint and real L7 relay. The synthetic credential is redacted in evidence.
- **Label**: `bug`.

## What breaks

An administrator gives `realtime` access to binaries A and B, with
`allow_uninspected_credentials` disabled. A later update names only A and enables
the flag. The update succeeds, and the shared endpoint now carries the exception
for B as well, although B was not declared by the update. This defeats the
per-binary authorization scope: a credential-bearing binary WebSocket frame from
B changes from a policy close to an upstream-forwarded frame.

The scope contract is documented in OpenShell's
[policy management guide](https://github.com/NVIDIA/OpenShell/blob/c0eb3dbd30b50e7e24666af857b8320e9d83a6cc/docs/how-it-works/policies/manage-policies.mdx#L108-L111): unnamed binaries must not inherit newly granted permissions. The code also applies that inheritance check to the sibling `websocket_credential_rewrite` flag, and rejects the same update shape. The credential exception is intended to be an explicit opt-in, not a shared endpoint-wide grant to binaries omitted from an update.

## Wire and policy evidence

Raw CLI commands, stdout and stderr, before/after effective policies, per-trial
results, sanitized relay records, and frame bytes are retained under
`transcripts/090/`. `policy/results.json` is a summary; each trial directory
contains its source artifacts. Captures are preserved as received from the
reproduction runs.

| Target | A-only flag update | Flag-removed control | Sibling-flag guard | Live relay off/on |
|---|---:|---:|---:|---:|
| v0.1.2 `6648bd0` | 5/5 inherits for B | 5/5 unchanged | 5/5 rejected | 3/3 denied, 3/3 forwarded |
| main `c0eb3db` | 5/5 inherits for B | 5/5 unchanged | 5/5 rejected | 3/3 denied, 3/3 forwarded |

### Target fingerprints

- Release CLI asset SHA-256: `cdde7e92bd7eac664031cf171cfe80d29e7f122a6674917b25a4ce0bcbc33466`; extracted executable: `789093ba9278271f2617642cadfabe58d3c08f9f8a5a11c29dcbd4ab60d6611f`.
- Release gateway asset SHA-256: `640068efa16e446d5f4f9ffaec0af769dbab04d686473d2a7bd6bafeb4ef7f45`; extracted executable: `b4ac81f235daa12a9168fccaa5558aecfe738b1c3779ea72cb0c66b9b8253e25`.
- Release supervisor and sandbox image IDs: `sha256:d7b5264bb6bc56f4796e6fa3617b8e4a8d785be0b7293542efd8cc250b0fb67a` and `sha256:bf4797b6c511f2d8ba02955dbba4bf76c1f0dd6d83531420c5408d5f1fb9d72f`.
- Current-main CLI and gateway were built from the exact source commit above with Rust/Cargo 1.96.0. The supervisor and sandbox images were `sha256:401477747ea6b081feb3c63e37d05fbbdbf913af036ff74a3caa1dbaee655513` and `sha256:ef6d6f8d5b4c56f646cc642134743f86ab7aa18c8d3382ccea33227a96e14b51`; both have SLSA provenance for source commit `cf1bbb965d29ca9c5fb31c0d26624f7bd7ae1411`. Docker Engine was 29.3.1, Linux aarch64 inside the sandbox.

With the exception off, B's credentialed binary frame receives WebSocket close
1008 and no binary frame reaches the capture upstream. After the A-only update,
B sends the same synthetic payload and the relay forwards it byte-for-byte. Both
relay states confirm that the Authorization header was present while its value
was redacted. The sandbox ran Ready and executed B as a process inside the
sandbox; this is not a mock-only result.

## Reproduction

Use the supported OpenShell Docker gateway and a Ready sandbox with the policy in
`transcripts/090/seed-policy.yaml`. Build or install the CLI and gateway at the
pinned target. Keep the local gateway and sandbox running, then run the rig into
a new output directory (it refuses to overwrite one):

```sh
python3 transcripts/090/reproduce.py \
  --openshell "$OPENSHELL_BIN" --gateway "$GATEWAY" --sandbox "$SANDBOX" \
  --seed-policy transcripts/090/seed-policy.yaml \
  --output-dir "$OUT/policy" --target-version "$TARGET_VERSION" \
  --target-commit "$TARGET_COMMIT" --trials 5

python3 transcripts/090/reproduce_relay.py \
  --openshell "$OPENSHELL_BIN" --gateway "$GATEWAY" --sandbox "$SANDBOX" \
  --seed-policy transcripts/090/seed-policy.yaml \
  --server-script transcripts/090/mock_ws_server.py \
  --output-dir "$OUT/relay" --target-version "$TARGET_VERSION" \
  --target-commit "$TARGET_COMMIT" --trials 3
```

Run once for each target. The release CLI and gateway checksums, current-main
image provenance, environment variable names, and build identity are recorded
in the fingerprints above and `transcripts/090/` metadata. Do not put a real
credential in the seed policy; the rig uses a synthetic credential and records
only its presence and a redacted value.

## Root cause

In upstream `crates/openshell-policy/src/merge.rs`,
[`endpoint_attributes_cover`](https://github.com/NVIDIA/OpenShell/blob/c0eb3dbd30b50e7e24666af857b8320e9d83a6cc/crates/openshell-policy/src/merge.rs#L817-L912)
compares endpoint authorization flags but omits `allow_uninspected_credentials`.
[`merge_endpoint`](https://github.com/NVIDIA/OpenShell/blob/c0eb3dbd30b50e7e24666af857b8320e9d83a6cc/crates/openshell-policy/src/merge.rs#L1666)
then widens it with `|=`. Since the inheritance check relies on the coverage
comparison, it does not retain the update on its own A-only rule. At runtime the
L7 relay checks this exception before forwarding credentialed frames in
[`relay.rs`](https://github.com/NVIDIA/OpenShell/blob/c0eb3dbd30b50e7e24666af857b8320e9d83a6cc/crates/openshell-supervisor-network/src/l7/relay.rs#L1767-L1768).

**Maintainer fix**: Include `allow_uninspected_credentials` in endpoint authorization coverage so an update enabling it must declare every existing binary that would inherit it.

## Bug-or-not checks

- **Expected behavior**: the documented policy-management contract says unnamed binaries do not receive newly granted permissions; the sibling-flag control confirms that the existing inheritance guard enforces this for another authorization flag.
- **Maintainer ruling**: PRs #2499 and #2493 establish the prior guard and later-added flag; no commit, pull request, or issue found that deliberately exempts this flag. Search also covered the related closed issue #2497.
- **Supported usage**: standard policy update through the public CLI, documented binary declarations, supported credential binding, and the local Docker gateway; no disabled authentication or unsupported provider layout.
- **Boundary crossed**: an operator-declared per-binary credentialed-network boundary inside the sandbox. B is not named in the grant update, yet its credentialed WebSocket frame is forwarded.
- **Label**: `bug`.

## Upstream status

Checked 2026-09-30 against release v0.1.2 and current `main` at
`c0eb3dbd30b50e7e24666af857b8320e9d83a6cc`. Search terms included
`allow_uninspected_credentials`, `uninspected credentials binary`,
`ExistingBinariesWouldInheritAuthorization`, `also declare authorization`,
`websocket credential binary`, and `policy merge binary scope`; open and closed
issues, pull requests, commits, release notes, changelog, and policy docs were
checked. Relevant results are #2499, #2493, and #2497; #3129 is a different
provider-credential path. Classification: **regression**. The earlier guard
does not cover a later-added permission flag.

## Frozen invariant

A policy update that widens a credential authorization exception on an existing
shared endpoint must not make that exception effective for a binary omitted
from the update. The harness checker validates complete policy and live-relay
matrices, including the no-flag and sibling-flag controls, rather than matching
a particular OpenShell source line.

## Validation

| Check | Result |
|---|---|
| v0.1.2 policy failing/control/sibling matrices | 5/5 each |
| Current-main policy failing/control/sibling matrices | 5/5 each |
| v0.1.2 live sandbox relay flag-off / flag-on | 3/3 each |
| Current-main live sandbox relay flag-off / flag-on | 3/3 each |
| Focused evidence checker | Passed for both targets, including two falsification mutations |
| `cargo test --workspace` | Passed, 221 tests |
| `cargo fmt --all -- --check` | Passed |
| `cargo clippy --workspace --all-targets -- -D warnings` | Passed |
| `python3 tools/update-readme-counts.py --check` | Passed, 66 folders and 221 tests |
| Independent reviewer | Pending |

## Verdict

- Correctness: PASS on pinned release and current main, with real CLI, gateway,
  Ready sandbox and relay evidence.
- Usefulness: PASS; the undeclared binary crosses the credentialed WebSocket
  boundary, demonstrated by the 1008-versus-byte-identical-forwarding controls.
- Upstream status: PASS, regression.
- Overall: NEEDS EVIDENCE until independent review and all repository checks
  complete.
