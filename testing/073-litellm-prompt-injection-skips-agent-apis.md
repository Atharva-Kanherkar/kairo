# 073-litellm-prompt-injection-skips-agent-apis Test Contract

## Functional Behavior

- With documented `callbacks: ["detect_prompt_injection"]`, a client request
  whose body contains LiteLLM's heuristic injection marker must be rejected
  with HTTP 400 and must not be forwarded upstream.
- The same LiteLLM 1.99.0 process must reject that marker on
  `/v1/chat/completions` and must forward it on `/v1/messages` and
  `/v1/responses`.
- Ordinary prompts without the marker must still complete on all three routes,
  so the finding is a skip, not a dead proxy.
- Record five trials per cell. Occupied reproduction ports and a LiteLLM
  process that exits during startup must fail fast.
- Keyless `repro.py` must not load repo `.env`. Live `live_repro.py` may read
  `OPENAI_API_KEY` from the environment or `.env` with interpolation off, and
  must never persist the value.
- Live OpenAI confirmation must send Chat, Messages, and Responses through a
  local relay whose only authenticated destination is `api.openai.com`. Direct
  OpenAI Chat with the same marker must return HTTP 200, proving the Chat 400
  is LiteLLM's detector.
- Live client completions must be sanitized. Provider response text must not
  be committed. The issue artifact must not include provider credentials.

## Unit Tests

- `prompt_injection_blocked_before_upstream` reports `Conformant` for HTTP 400
  with no upstream forward of the marker.
- The checker reports `Violation(PROMPT_INJECTION_REACHED_UPSTREAM)` when the
  marker appears in `upstream`.
- The checker reports `Violation(PROMPT_INJECTION_NOT_REJECTED)` when the
  client accepts the marker with HTTP 200 and no upstream body.
- A prompt without the marker is `Conformant` even at HTTP 200.
- A sanitized live envelope that still forwards the marker is
  `PROMPT_INJECTION_REACHED_UPSTREAM`.
- Malformed JSON and an empty marker must not be reported as
  `PROMPT_INJECTION_REACHED_UPSTREAM`.

## Integration / Functional Tests

- `litellm_prompt_injection_blocks_chat_completions` checks five Chat
  injection controls for route identity, status 400, null upstream, and
  conformance.
- `litellm_prompt_injection_skips_anthropic_messages` and
  `litellm_prompt_injection_skips_responses` check five violations each for
  route identity, status 200, upstream `/v1/responses`, and the leak reason.
- `litellm_prompt_injection_safe_prompts_still_complete` checks five benign
  completions per route.
- Matching `live_*` tests replay `transcripts/073/live/` and require OpenAI
  `provider_status` 200 plus sanitized client completions on success paths.
- `litellm_prompt_injection_live_openai_accepts_marker_directly` checks the
  direct OpenAI control.
- `litellm_prompt_injection_route_identity_rejects_swapped_capture` keeps a
  Chat control from counting as a Messages violation.
- `cargo test --workspace` passes.

## Smoke Tests

- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes.

## E2E Tests

- `tools/litellm-env/bin/python transcripts/073/repro.py` reproduces 5/5 on
  LiteLLM 1.99.0 against a local capture upstream.
- `tools/litellm-env/bin/python transcripts/073/live_repro.py` reproduces 5/5
  on LiteLLM 1.99.0 against real OpenAI when `OPENAI_API_KEY` is present.

## Manual Tests

- Enable `detect_prompt_injection`, send the marker on Chat, Messages, and
  Responses, and confirm only Chat returns 400 with no upstream POST.
