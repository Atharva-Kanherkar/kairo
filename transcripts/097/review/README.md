# 097 independent review evidence

Recorded 2026-10-10 by the independent reviewer, following
`.github/agents/kairo-reproduction-reviewer.agent.md`. Nothing here was produced
by the author's rig runs. No credential appears in any file, and no live provider
was called: the whole finding is keyless by construction, so the reviewer's
commands are the author's documented ones plus its own build, its own output
directory, and its own recorder.

Verdict as delivered: `NEEDS EVIDENCE`, blocked on one mechanical finding: the
README and SCOREBOARD folder counts were left stale against an actual count, so
`python3 tools/update-readme-counts.py --check` exited 1. All three gates
passed. The count was refreshed and the check now exits 0.

Note on numbering: the review ran while this finding was numbered 095 on a
side branch. `origin/main` has since merged findings that claimed 095 and 096, so
this pull request carries the finding as 097. The commands below are quoted as
the reviewer ran them, with the paths it actually used, and the review's
substance is unaffected by the renumbering.

## What the reviewer did

Built the target itself rather than trusting the author's binary:

```
cd /Users/atharva/llm-d-router && go build -o /tmp/review-095/build/coordinator ./cmd/coordinator
go version -m /tmp/review-095/build/coordinator   # go1.27.2, mod v0.10.0-rc.1.0.20261009160539-567e35d752c8
python3 transcripts/097/reproduce.py \
  --target /Users/atharva/llm-d-router --binary /tmp/review-095/build/coordinator --output /tmp/review-095/out
```

The last command was run from this worktree with the venv that provides
`jsonschema` 4.25.1, `/tmp/kairo-095-venv/bin/python`, which is the same
`--target`, `--binary`, `--output` interface the reviewer used.

The reviewer's binary and the author's `/tmp/llm-d-coordinator-567e35d` are
byte-identical (sha256 `8fd544dd…`), and the pinned checkout was still clean at
`567e35d752c841521fd7c450e8335e5dae108a7c`.

## Reproduced results

| Check | Result |
|---|---|
| `reproduce.py`, reviewer's own binary and output | `{"large_schema_corrupted": 5, "safe_controls": 5, "direct_controls": 5, "runs_per_case": 5}` |
| Reviewer's `results.json` versus the committed one | all 15 run records identical |
| Reviewer's captures versus the committed captures | all 120 files byte-identical after normalising `Host:`, `X-Llm-D-Revision-Decision-Id`, and `Date:` |
| Boundary sweep, nine magnitudes by three legs | identical to `../capture/boundary/boundary-sweep.txt` |
| Coordinator log | 10 requests, zero occurrences of error or warn |

## Attribution attacks

| Attack | Result |
|---|---|
| Same route, identifier 42 | preserved 5/5, validation clean 5/5 |
| Coordinator removed, same request | preserved 5/5, validation clean 5/5 |
| Is the capture upstream the rounding site | No. `reproduce.py` echoes the literal it parsed from the forwarded body; Python's `json` keeps big integers exact. Confirmed with the reviewer's own recorder that does no validation |
| Could a Go map marshal alone explain it | No. Scratch Go probe: default `Unmarshal` plus `Marshal` emits `9007199254740992`; the same map built with different insertion orders emits the same value; `UseNumber` plus `Marshal` emits `9007199254740993` |
| Does any step rewrite `response_format` | No. `grep -rn "response_format" pkg/coordinator/` returns nothing; `render.go:221`, `prefill.go:138-141`, and `decode.go:94-98` write only `prompt`, the budget and the transfer fields |
| Is the pinned commit already fixed | No. Open PR 3141's `inspectedRequestFields` excludes `response_format`, so it would fix this, but it is unmerged. Upstream `main` at `169629369ed8` (2026-10-10) still has `json.Unmarshal` at `handlers.go:124-125` |
| Reviewer's own extra probe | top-level `seed`, `const`, and `minimum` all round on the render leg and all survive with the coordinator removed, so the defect is not schema-specific |

Root cause independently confirmed in the pinned source at
`pkg/coordinator/server/handlers.go:124-125`, with the three marshal sites at
`pkg/coordinator/steps/render.go:333`, `pkg/coordinator/steps/prefill.go:86`, and
`pkg/coordinator/steps/decode_proxy.go:54`. Both quoted project comments are
verbatim correct.

## Gate verdicts

- **Correctness: PASS.** Exact claim, exact commit, real entry point, real
  binary, 5/5 per case, controls 5/5, byte-identical to the committed evidence.
- **Usefulness: PASS.** The reviewer agreed the measured consequence is
  sufficient without inference, because a single-element `enum` forces the
  engine's value arithmetically: the client accepts only `9007199254740993` and
  the forwarded schema admits only `9007199254740992`, so no engine behaviour can
  close the gap. It flagged as a coverage gap that the weaker bound variants
  (`minimum`, `maximum`, `multipleOf` without a pin) are asserted in one sentence
  without a measured consequence; the recorded claim is the single-enum case.
  Label `bug`, with the fix being one line.
- **Upstream status: PASS.** Same-day searches across 20 terms plus issue and
  pull request reads. Agrees with `discussed-no-ticket` and notes that
  `duplicate-open` is also arguable because #2621 states the same expectation;
  both are in the allowed set and neither is a rejection reason.

## Findings

| Item | Where | Status |
|---|---|---|
| Folder counts stale against the actual count | `README.md`, `issues/SCOREBOARD.md` | fixed; `--check` exits 0 |
| `value_diff` matched a declared field at every depth, exempting a rounded integer inside any nested object keyed `max_tokens`, `stream`, `kv_transfer_params`, or `max_completion_tokens` | `crates/harness/src/checks.rs` | fixed; allowlist is top-level only, pinned by a new unit test |
| Writeup said the coordinator log covers the whole matrix; it covers the ten coordinator-routed runs | `issues/097-llm-d-coordinator-schema-integer/README.md` | corrected |
| Two quoted comments cited with end lines off by one and two | the same writeup | corrected |
| `boundary_sweep.py` hardcoded its output path, so two reviewers clobbered each other | `transcripts/097/boundary_sweep.py` | fixed; `--output` added |
| Values above `u64::MAX` cannot be compared exactly and this was unstated | `crates/harness/src/checks.rs` | documented |

The reviewer also ran the repository checks from a pristine tree: `cargo test
--workspace`, `cargo fmt --check`, and `cargo clippy --workspace --all-targets`
all passed. It falsified the harness coverage in a scratch copy outside the
worktree: flipping the `large-01` render bytes to preserve the value flips three
conformance tests to FAIL, and changing `safe-01` to forward a different integer
flips its test to FAIL, so the coverage is not vacuous.
