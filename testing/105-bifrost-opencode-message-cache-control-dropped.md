# 105 hunt strategy and review contract

Date: 2026-10-19. Target: maximhq/bifrost, latest release `transports/v2.3.0`
(`9237ec06`, core `v1.12.0`), default branch `dev` at `1813de533` for the struct
check.

## Strategy

The bar set for this hunt was a defect worth at least 8 out of 10 to a production
user, on a released version, with default settings. Lanes run the same day and
found clean on Bifrost v2.3.0, each with a working control:

- Governance: virtual-key model allowlists on `/v1`, `/openai`, `/anthropic`,
  Responses, embeddings and completions; request-body `fallbacks` to a blocked
  model; $0.10 budgets on seven routes including streamed `/v1/responses`.
- MCP: ungranted and read-only virtual keys against `/mcp`, `/mcp/{slug}` and
  `/v1/mcp/tool/execute`; agent-mode auto-execution of a forbidden tool; client
  declared tools passing through.
- Object ownership: cross-key file get, content and batch creation on `/v1` and
  `/openai`.
- Bedrock prompt caching was skipped: eight reports since September cover it
  (#6933, #7336, #7613, #7614, #8215, #8541, #8542, #8554).

Bots sweep translation paths thoroughly, so the next lane was a real agent client
end to end on Bifrost's documented setup: opencode, one of the four harnesses
Bifrost ships a launcher for, on Claude, GPT and Gemini models. The opencode runs
on Claude showed 0 cache reads and writes on every step while GPT and Gemini cached
24k to 26k tokens per step, which led here.

## Kill criteria

1. The trigger must come from a real client on a supported, documented Bifrost
   path with default settings. Otherwise `operator-misuse` or `hardening`.
2. Removing only the trigger must pass, and removing only the gateway must pass.
3. The consequence must show up in the provider's own usage, not only in bytes.
4. No upstream issue or pull request may already cover it. #2469 and #3942 were
   read in full and are different defects.

## Review contract

A reviewer accepts only after rerunning, at minimum:

- The forwarded-bytes check: one opencode request through a Bifrost v2.3.0 binary
  to a recording relay on the Anthropic or OpenRouter provider, showing the client's
  message-level markers absent upstream, and the block-level variant present.
- One live replay pair through Bifrost and one direct, showing 0 cached tokens on
  the Bifrost second call and a cache read on the direct one.
- `cargo test -p kairo --test conformance bug105` and the checker unit test.

Known gaps the reviewer should weigh: no live native Anthropic replay (account
without credit on the capture day), Bedrock and Vertex untested, and the session
cost ratio measured only on five-step sessions.
