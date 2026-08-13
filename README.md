# kairo

**Does the wire protocol survive the pipe?**

Every LLM gateway, proxy, and OpenAI-compatible façade claims compatibility.
None of them prove it. kairo is a regression-driven conformance harness -
and eventually the router that passes it, built from *real, cited, still-open
bugs* in the tools people run in production.

## Methodology

1. **Pick a cited, open issue** in a real tool (LiteLLM, Ollama, Switchyard,
   vLLM, SGLang, Bifrost…), see [issues/TARGETS.md](issues/TARGETS.md).
2. **Reproduce it live** with real API keys / real local models. Capture the
   raw wire bytes (request + SSE response) on both sides of the pipe.
3. **Write it down** in `issues/NNN-slug/`, what breaks, why, upstream link.
4. **Freeze it as a replay test**: recorded provider bytes go in, the tool's
   output is diffed against what a lossless pipe must emit. No network, no
   keys, no flakiness, runs in CI forever.

Regressions become the spec. The router we build must pass all of them; the
tools we mined them from demonstrably don't.

## Layout

```
crates/harness/    replay runner: feeds recorded SSE through a target, diffs
issues/            one folder per reproduced bug: writeup + fixtures + test
transcripts/       captured golden wire transcripts (provider ground truth)
.env               provider keys, capture runs only, never committed
```

## Quick start

```
cp .env.example .env   # fill in your keys (capture only)
cargo test             # replay suite (no keys needed)
```

## Conformance harness

`crates/harness/src/checks.rs` holds the invariant checkers; each returns
`Conformant` or `Violation(reason)`. `crates/harness/tests/conformance.rs` runs
them against recorded transcripts and asserts the verdict, a `Violation`
assertion is a reproduced bug, frozen. The same checker run against a correct
implementation returns `Conformant`; that is how the suite scores any target.

Checkers so far: Anthropic tool-call `stop_reason`, OpenAI stream
`finish_reason`, tool-call id charset. Fixtures: LiteLLM 1.82 vs 1.96
(regression pair), `ollama_chat/` stream, Gemini 3 signature-in-id.

Status: 13 reproduced defects in `issues/` (one filed as Switchyard #380);
harness green with 4 conformance checks wired to recorded bytes.

## Contributing

Proving a bug is real is the contribution; no fix required. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the capture, control, replay method and
[issues/TARGETS.md](issues/TARGETS.md) for unclaimed targets.
