# Independent review of finding 090

Reviewer: a separate agent session working from `.github/agents/kairo-reproduction-reviewer.agent.md`.
Date: 2026-09-30 (UTC). PR head reviewed: `c1161ad5b27dfc4487818e932f74da959443d504`.

Independence limits: the reviewer built its own gateway, PKI, image, provider
and sandbox from scratch and did not reuse the author's containers. It ran on
the same Mac and the same Docker Desktop install as the author, so it is not a
different host. No credential was used or recorded. The only secret-like value
is a synthetic provider token, `synthetic-review-token-not-a-secret`.

```text
Overall: ACCEPT

Gate 1, correctness: PASS
Gate 2, usefulness: PASS
Gate 3, upstream status: PASS
Repository checks: PASS
Blocking findings: none
```

## Gate 1, correctness: PASS

**Claim tested.** `openshell policy update` naming only binary A, with
`--add-endpoint ...:allow-uninspected-credentials`, turns the flag on for the
shared endpoint, so undeclared binary B inherits it and its credentialed binary
WebSocket frame is forwarded.

**Independent setup.** The PR's own `reproduce.py` and `reproduce_relay.py`,
run against a reviewer-owned gateway and Ready Docker sandbox:

| Target | Identity checked |
|---|---|
| v0.1.2 | CLI asset `cdde7e92...3466` and gateway asset `640068ef...7f45` match the writeup; extracted executables `789093ba...611f` and `b4ac81f2...3e25` match |
| dev channel, main `ba16b9f2c` | `openshell 0.1.3-dev.25+gba16b9f2c`, CLI and gateway from the rolling `dev` release, `dev` supervisor and sandbox images pulled the same day |

The dev target is newer than the PR's pinned main (`c0eb3dbd`). Upstream
`crates/openshell-policy/src/merge.rs` has no change between `c0eb3dbd` and
main HEAD `252882f`.

**Reproduction, N of N.** Counts are recomputed from the raw `before`, `after`
and `update` files, not from `passed` flags:

| Case | v0.1.2 | dev ba16b9f2c |
|---|---:|---:|
| A-only update with the flag: B ends with the flag on, exit 0 | 5/5 | 5/5 |
| Same update without the flag: unchanged, exit 0 | 5/5 | 5/5 |
| Sibling `websocket-credential-rewrite`: rejected, "also declare /usr/local/bin/tool-b" | 5/5 | 5/5 |
| Live relay, flag off: close 1008, no upstream frame | 3/3 | 3/3 |
| Live relay, flag on: byte-identical binary frame at upstream | 3/3 | 3/3 |

Byte comparison with the PR on v0.1.2: the effective policy objects after the
update are identical, the policy hashes match (`a4df93d32434` failing,
`bdacf5e3b28c` trigger removed), the forwarded payload is identical
(`4b4149524f2d3039302d42494e4152592d414354494f4e`), and the upstream
handshake is identical apart from `Sec-WebSocket-Key`.

**Attribution attacks (raw files in `extra-controls/` and `credential-check/`).**

| Attack | Result |
|---|---|
| Declare both A and B with the flag | succeeds, policy hash `a4df93d32434`, identical to the A-only update. The A-only update has exactly the effect of granting B. |
| A-only update that adds a new `allowed-ip` | rejected with the same "also declare tool-b" error, so the guard works for other widening fields |
| Drop `allowed-ip` from the failing update | still inherits, policy hash `a4df93d32434`. The flag alone is the smallest trigger. |
| Is the forwarded connection really credentialed? | upstream saw the resolved provider secret, not the placeholder, in both modes (`auth_equals_provider_secret: true`, `auth_is_placeholder: false`). Only booleans were recorded. |
| Different version | same result on v0.1.2 and on dev main |

**Source.** On main, `endpoint_attributes_cover` compares
`allow_encoded_slash`, `websocket_credential_rewrite` and
`request_body_credential_rewrite` with `flag_covers`, and omits
`allow_uninspected_credentials`, while `merge_endpoint` widens it with `|=`.

Setup notes: the Docker driver refuses plaintext gateways
("docker sandboxes require launch-scoped gateway authentication"), so the
gateway must run with mTLS. Docker Desktop also needs host networking enabled,
as the OpenShell runtime docs state. `gateway-logs/gateway-v0.1.2.txt` still
shows six "Sandbox failed to become ready" lines from the attempts made before
host networking was on. The steps are in the writeup's cold-start section.

## Gate 2, usefulness: PASS

- **Affected user and workflow.** An administrator granting credentialed
  network access to selected binaries of a multi-binary rule with
  `openshell policy update`.
- **Demonstrated consequence.** B's frame goes from a policy close (1008) to
  forwarded, byte for byte, through the real supervisor relay in a Ready
  sandbox.
- **Measured versus inferred.** Measured: 3/3 forwarded versus 0/3 with the
  flag off, on two targets. Inferred, and labelled so in the PR: the wider
  workflow impact. Severity is modest, because B can already open the
  credentialed connection. The flag adds uninspected binary frames.
- **Bug or not.** Label `bug`.
  - The doc lines the PR quotes describe `--add-allow`. The spec for
    `--add-endpoint` rests on upstream PR #2499 and issue #2497, and on the
    sibling-flag rejection, which the reviewer reproduced.
  - No maintainer ruling exempts this flag. The upstream test for the flag
    merges into a rule with no binaries.
  - The trigger is documented usage on an mTLS-authenticated gateway.
  - Fix in one sentence: add `flag_covers(loaded.allow_uninspected_credentials,
    proposed.allow_uninspected_credentials)` to `endpoint_attributes_cover`.

## Gate 3, upstream status: PASS

- **Checked.** 2026-09-30, v0.1.2 and main through `252882f`.
- **Searches.** `allow_uninspected_credentials`, `uninspected credentials`,
  `endpoint_attributes_cover`, `ExistingBinariesWouldInheritAuthorization`,
  `authorization inheritance`, `binary inherit`, and five synonym phrases, over
  issues, pull requests, commits to `merge.rs` since #2499, and docs. No open
  PR touches `merge.rs`.
- **Result.** No dedicated report and no fix. #2499 (merged 2026-08-10) added
  the guard, #2493 (merged 2026-08-19) added the flag and the `|=` without a
  coverage change, #2497 is the closed general issue, and #3129 (closed,
  unmerged) is a different path.
- **Label note.** The PR calls this `regression`. No release ever rejected an
  A-only flag update, because the flag arrived after the guard, so `novel`
  with #2499 and #2497 as related is arguably more exact. The defect is current
  either way.

## Repository checks: PASS

`repo-checks.txt`: README counts match (66 folders, 221 tests), `cargo fmt`
clean, `cargo test --workspace` 41 unit and 180 conformance pass, clippy with
`-D warnings` clean. The diff has no em dashes, absolute local paths or
credentials. The reviewer also fed the checker extra mutations in a scratch
copy (dropped trial, B declared, exit code, flag flipped, sibling accepted,
payload mismatch, wrong close code, missing authorization, wrong outcome,
flag-off-only relay, empty input). All were rejected except the two gaps in
observation 3. The test source is `checker_mutations.rs.txt`.

## Non-blocking observations

1. The writeup's Reproduction section omitted the image build, profile
   import, provider create, sandbox create, mTLS gateway and Docker host
   networking steps. Added in the follow-up commit.
2. The PR body and the testing contract said gateway logs were in
   `transcripts/090/`, but none were committed. The reviewer's gateway logs
   are in `gateway-logs/`, and the PR body was corrected.
3. Checker gaps against its own contract: it does not check that the sibling
   control was rejected for the right reason (any non-zero exit passes), it
   does not scan for unsanitized values, and `binary_*_declared_by_update` is
   a constant written by the rig.
4. `reproduce_relay.py` has no timeout on the client `sandbox exec`. One
   reviewer attempt hung for over ten minutes right after Docker was
   restarted, and a rerun was clean. The cause was not attributed.
5. The mock server redacts the `Authorization` value, so the PR's own
   evidence cannot show that the resolved secret reached the upstream.
   `mock_ws_server_credcheck.py` records two booleans to close that gap.
6. The SCOREBOARD coverage sentence omitted OpenShell. Fixed in the
   follow-up commit.

## Not verified

- A rebuild of OpenShell main from source. The dev channel was used instead.
- A rerun at main HEAD `252882f`. `merge.rs` is unchanged through it.
- A different host or a Linux Docker engine. The seed policy hardcodes the
  Docker Desktop host address `192.168.65.254`.

## Files

- `v0.1.2/`, `dev-ba16b9f2c/`: full reviewer output of `reproduce.py` and
  `reproduce_relay.py`.
- `extra-controls/`: `both-declared.txt`, `no-allowed-ip.txt`,
  `new-allowed-ip.txt`.
- `credential-check/`: relay runs with `mock_ws_server_credcheck.py`, flag off
  and flag on, on the dev target.
- `gateway-logs/`: gateway output with ANSI codes stripped and the scratch
  path replaced by `$RUN`.
- `checker_mutations.rs.txt`: the reviewer's extra checker mutations, kept as
  text so it is not compiled into the harness.
- `repo-checks.txt`: fmt, test, clippy and README count results.
