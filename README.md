# kairo

**A conformance suite for LLM tool-call translation.**

Every LLM gateway, proxy, and OpenAI-compatible server claims compatibility.
Almost none of them prove it, and in practice tool calls are quietly corrupted
in translation: arguments dropped, ids mangled, finish reasons mislabeled,
images turned into text the model never sees. The failures are usually silent
(HTTP 200), so agent loops break and the model gets blamed.

kairo reproduces these failures on the wire, freezes each one as a
deterministic replay test, and scores any translation layer against them. It is
built from real, cited bugs in the tools people run in production, with the
recorded bytes to prove each one.

The end goal is a router that lets you run any coding agent on any model with
tool calls that actually work. That router has to be built on data, not guesses,
so this repo is also an open dataset of real tool-call failures.

## Who this is for

If you point Claude Code (or Cline, Cursor, Aider, OpenClaw) at a cheaper or
local model to save money, DeepSeek, Kimi K2, GLM, Qwen, a local Ollama model,
through a gateway like LiteLLM, OpenRouter, claude-code-router, or Switchyard,
you have probably hit this: a tool call that silently does nothing, a file that
never gets written, an agent that stalls after calling a tool. It usually is not
the model. It is the gateway mistranslating the tool call between the client's
format and the model's.

kairo is, today, the receipts that explain why your setup is flaky, and, as the
dataset grows, the foundation for a router that fixes it.

## Two ways to help

1. **Donate a broken transcript.** You do not need to know why it broke or
   write any code. A real example of a real failure is the open data this
   project runs on.

   The easiest way: install the `/kairo-report` command into Claude Code once,

   ```
   mkdir -p ~/.claude/commands && curl -fsSL https://raw.githubusercontent.com/Atharva-Kanherkar/kairo/main/agent-commands/claude-code/kairo-report.md -o ~/.claude/commands/kairo-report.md
   ```

   then, the next time a tool call breaks, type `/kairo-report` in that same
   session. Your agent gathers the evidence, redacts secrets, shows you the
   report, and files it after you confirm. Zero copy-pasting.

   Prefer the manual route? Open a
   [tool-call failure report](https://github.com/Atharva-Kanherkar/kairo/issues/new?template=tool-call-failure.md)
   and fill in what you can.
2. **Reproduce and freeze a bug.** If you like breaking things, pick an
   unclaimed target and turn it into a checked, replayable test. Proving a bug
   is real is the whole contribution; no fix required.

## Status

<!-- kairo-counts:start -->
32 distinct defects reproduced and documented across LiteLLM, NVIDIA Switchyard, Bifrost, and GoModel (see [`issues/SCOREBOARD.md`](issues/SCOREBOARD.md)). One is already filed upstream as [NVIDIA-NeMo/Switchyard#380](https://github.com/NVIDIA-NeMo/Switchyard/issues/380).
The Rust harness is green with 87 tests, of which 76 are conformance checks wired to recorded transcripts. This is early and active; contributions of new reproductions are the fastest way to help.
<!-- kairo-counts:end -->

## How it works

Each finding is held to a three-part standard:

1. **Wire evidence.** The actual bytes, in [`transcripts/`](transcripts). Raw
   SSE or JSON, not screenshots.
2. **A control that works.** The same input succeeding somewhere (the model
   called directly, another route, another version), which isolates the
   translation layer as the cause rather than the model or the prompt.
3. **Determinism.** N of N reproductions, with the trigger narrowed as tightly
   as possible.

A reproduction becomes a checker in
[`crates/harness/src/checks.rs`](crates/harness/src/checks.rs) that returns
`Conformant` or `Violation(reason)`, plus a test in
[`crates/harness/tests/conformance.rs`](crates/harness/tests/conformance.rs)
that asserts the verdict against the recorded bytes. Run against a buggy
gateway the checker reports the violation; run against a correct implementation
it reports conformance. Same checker, both directions. That is how the suite
scores a target instead of arguing about it.

Two capture rigs cover most cases. A **live capture** runs the gateway locally
and records the bytes both directions. An **offline capture rig**
([`tools/mock_upstream.py`](tools/mock_upstream.py)) points the gateway's
backend at a mock that logs exactly what the gateway forwards upstream, which
exposes encode-side losses with no API keys and full determinism.

## Quick start

Run the suite (no keys required, it replays recorded bytes):

```
cargo test
```

Reproduce a bug live (keys used only for capture, never committed):

```
cp .env.example .env   # add your provider keys
# then follow the repro block in any issues/NNN/README.md
```

## Layout

```
crates/harness/    Rust replay harness: invariant checkers + conformance tests
issues/            one folder per finding: writeup, proof, invariants
issues/SCOREBOARD.md   every finding at a glance, with reproduction status
transcripts/       recorded wire bytes (the evidence)
tools/             capture rigs and local reproduction helpers
.env               provider keys for capture only (gitignored)
```

## Scope

The focus is tool calling, because that is where agent loops depend on the
translation being exact: streaming argument assembly, tool-call ids, finish and
stop reasons, multi-turn replay, and multimodal tool results. Findings span
Chat Completions, Anthropic Messages, and the Responses API, across cloud and
local backends.

## Contributing

New reproductions are the most valuable contribution, and proving a bug is real
is enough; a fix is not required. Non-reproductions (a cited bug that is patched
on current versions) are kept as data too.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the method and the pull-request
checklist, and [`issues/TARGETS.md`](issues/TARGETS.md) for a list of
unclaimed targets to start from.

## License

Apache-2.0. See [LICENSE](LICENSE).
