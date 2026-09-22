# 081, OGX silently drops adaptive thinking configuration

- **Upstream**: checked `ogx-ai/ogx` issues, pull requests, release notes, and
  the Anthropic Messages documentation on 2026-09-22. Merged [PR
  5938](https://github.com/ogx-ai/ogx/pull/5938) establishes fail-closed
  translation behavior for enabled thinking. It does not cover adaptive
  thinking, which still reaches the translation branch without a forwarded
  configuration. No matching adaptive-thinking ticket was found. Status:
  **novel**.
- **Target**: OGX v1.4.0, commit `051a8a0`, run from a pinned checkout.
- **Route**: Anthropic `POST /v1/messages` translated to an
  OpenAI-compatible `POST /v1/chat/completions` upstream.
- **Label**: **bug**.
- **Deterministic evidence**: `transcripts/081/ogx-adaptive-thinking-cases.json`
  records 5/5 HTTP 200 responses and the exact forwarded request bodies.
- **Control evidence**: `transcripts/081/ogx-enabled-thinking-control-cases.json`
  records 5/5 HTTP 400 fail-closed responses for the same route and prompt
  with only `thinking.type` changed to `enabled`.
- **Live direct-provider control**: `transcripts/081/live-anthropic-*` is
  produced by `live_anthropic_control.py` with the environment-provided
  `ANTHROPIC_API_KEY`. The key is never printed or written.

## Finding

With an OpenAI-compatible upstream configured, OGX accepts this supported
Anthropic request:

```json
{"model":"openai/mock-gpt","max_tokens":32,"thinking":{"type":"adaptive"},"messages":[{"role":"user","content":"Synthetic adaptive-thinking control."}]}
```

OGX returns HTTP 200, but the captured OpenAI request contains neither a
`thinking` field nor a `reasoning` or `reasoning_effort` configuration. The
client therefore cannot tell that the requested adaptive thinking was not
enabled. This is a real boundary loss for applications using the Anthropic
Messages contract with an OpenAI-compatible backend.

The trigger-removed control uses the same model, route, and synthetic prompt
with `thinking.type` set to `enabled`. It returns an Anthropic
`invalid_request_error` with HTTP 400 and sends no upstream request. This is
the fail-closed behavior described by merged PR 5938. Adaptive is accepted by
the same request model but bypasses that rejection.

## Root cause

In OGX v1.4.0 `src/ogx/providers/utils/inference/anthropic_translation.py`,
`anthropic_request_to_openai` rejects the enabled thinking variant for
translation mode but has no equivalent rejection or OpenAI mapping for the
adaptive variant. The adaptive object is therefore discarded while the rest
of the request is translated. The exact internal behavior is established by
the pinned source and wire capture, not inferred from the response text.

## Reproduction

The runner starts the capture upstream before OGX, starts the real `uv run ogx
go` process, waits for both readiness endpoints, runs five adaptive trials and
five enabled controls, and terminates both children. It writes to a temporary
directory unless `--refreeze` is explicitly supplied.

```sh
cd /Users/atharva/kairo-targets/ogx
uv sync
python3 /Users/atharva/kairo/transcripts/081/hunt.py /Users/atharva/kairo-targets/ogx
```

To intentionally refresh the checked-in deterministic evidence:

```sh
python3 /Users/atharva/kairo/transcripts/081/hunt.py \
  /Users/atharva/kairo-targets/ogx --refreeze
```

The invariant harness is
`ogx_adaptive_thinking_is_silently_ignored` and the checker unit tests in
`crates/harness/src/checks.rs`. They require exact N/N evidence, HTTP status,
non-empty forwarded messages, and absence of thinking or reasoning keys.
