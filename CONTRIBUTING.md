# Contributing to kairo

kairo grows one reproduced bug at a time. A contribution is a folder in
`issues/` with wire evidence, plus (ideally) a checker that freezes it as a
test. No fix required: proving a bug is real is the contribution.

## The method: capture, control, replay

Every accepted reproduction has three legs.

1. **Wire evidence.** The actual bytes, saved under `transcripts/`. Raw SSE or
   JSON, not screenshots, not paraphrases.
2. **A control that works.** The same input succeeding somewhere: the model
   called directly, another route, another version. The control is what pins
   the blame on the translation layer instead of the model or the prompt.
3. **Determinism.** N out of N runs, and the trigger isolated as tightly as
   you can (exact field, exact length, exact chunk shape). "Sometimes" is a
   lead, not a proof.

Non-reproductions are welcome too. "This cited bug is patched on current" is
recorded in `issues/SCOREBOARD.md` as data. A one-sided catalogue would be a
sales deck.

## How to add a reproduction

1. Pick a target from `issues/TARGETS.md` (or bring your own cited, open issue
   in any translation layer: LiteLLM, Switchyard, Bifrost, vLLM, SGLang,
   Ollama, claude-code-router, open-webui, ...).
2. Reproduce it. Two rigs cover most cases:
   - **Live capture**: run the gateway locally, send the request, save the
     bytes. See the repro blocks in any `issues/NNN/README.md`.
   - **Offline capture rig**: point the gateway's backend at
     `tools/mock_upstream.py` to record exactly what it forwards upstream.
     Zero keys, fully deterministic. See `issues/006` and `issues/007`.
3. Write it up: copy `issues/TEMPLATE.md` into `issues/NNN-short-slug/README.md`.
   State what breaks, the proof (all three legs), and the test invariants the
   bug implies.
4. Commit the transcripts under `transcripts/NNN/`. Strip anything private
   first. Never commit API keys; `.env` is gitignored for a reason.
5. If you can, add a checker in `crates/harness/src/checks.rs` and a test in
   `crates/harness/tests/conformance.rs` that asserts the verdict against your
   recorded bytes. `cargo test` must stay green.
6. Refresh the README counters so the Status bug and test numbers match the
   repo: `python3 tools/update-readme-counts.py`. CI fails the PR if those
   numbers are stale. On merge to main the same script runs again and commits
   if anything drifted.
7. Open a PR. One bug per PR.

## Required PR gates

Every pull request must use `.github/PULL_REQUEST_TEMPLATE.md` and independently
pass three gates before approval:

1. **Correctness.** The exact claim reproduces locally on the named version with
   raw wire evidence, a successful control, deterministic results, and an isolated
   trigger.
2. **Usefulness.** The wire defect is traced to a concrete failure in a real user or
   agent workflow. A byte difference alone is not enough.
3. **Upstream status.** Current issues, pull requests, releases, commits, and
   documentation have been searched and linked. The finding is labeled as novel,
   duplicate, fixed, regression, or documented behavior.

Use `.github/agents/kairo-reproduction-reviewer.agent.md` for the independent,
read-only review. It reruns the critical path and tries to disprove the claim. The
full mandatory rules and decision table are in `AGENTS.md`.

## What makes a good target

- Tool calls first: streaming argument assembly, ids, finish reasons,
  multi-turn replay, multimodal tool results. This is where agent loops die.
- Silent failures beat loud ones. HTTP 200 with corrupted content is the
  species we hunt.
- Cross-format paths (Anthropic in, OpenAI out, and every other pair) break
  more than same-format paths.

## Style rules

- No em dashes anywhere: files, commits, PR text.
- Plain, concise writing. State what you verified and what you did not.
- Cite upstream issues by number and check their open/closed state the day you
  write.

## Filing upstream

If your reproduction is novel, consider filing it on the tool's own tracker
(follow their bug template) and link it in your writeup. One kairo finding is
already filed as NVIDIA-NeMo/Switchyard#380.
