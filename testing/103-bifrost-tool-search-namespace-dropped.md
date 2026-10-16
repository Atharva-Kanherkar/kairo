# 103 hunt strategy and review contract

Date: 2026-10-16. Target: maximhq/bifrost, default branch `dev` at
`6c0f795da` and release `transports/v2.3.0` (core `v1.12.0`).

## Strategy

The bar is a defect that halts or silently corrupts a real agent workflow, not a
byte diff. Lanes were chosen from the code of the merged pull requests of the last
four weeks (about 200 merged PRs, diffs read where they touch request or stream
paths), from kairo's earlier Bifrost findings (030 to 037, 072, 075, 077 to 079,
082, 086, open 093), and from AGENTS.md's bug-or-not checks.

What the recent merges say:

- Maintainers are actively fixing the Chat to Responses to Anthropic stream
  bridge for Claude Code on self-hosted backends (#8574, #7699, #8090, #5959).
  About 22 providers use it.
- Tool search for Claude Code and Anthropic clients on OpenAI models landed on
  2026-10-14 and 2026-10-15 (#8502, #8503, #8504, #8505, #8555), fixing #8361.
  New, unreleased, and on the exact path Claude Code uses behind a gateway.
- Stream retry, TTFT fallback and startup-event buffering (#7503, #7504, #7976)
  are complex but heavily tested in the same PRs.
- #8602 fixed async raw-body aliasing; the remaining aliasing paths only reach
  logs, so no boundary is crossed.
- Already covered: Anthropic ingress field loss (030 to 037), semantic cache
  (086), MCP agent mode (082, 093), realtime auth (079).

Kill criteria for any lead, checked before writing anything up:

1. A real backend or client must produce the trigger through a supported,
   documented Bifrost path. Otherwise `hardening`.
2. A control with the same meaningful input must pass, and removing only the
   trigger must pass.
3. A real agent client must fail, not only the bytes.
4. No upstream issue or PR may already cover it.

## Lead A, not filed: chat-fallback streams keep only the first tool call per chunk

`core/schemas/mux.go` `ToBifrostResponsesStreamResponse` reads only
`delta.ToolCalls[0]`. Ollama with LFM2.5 (1.2B and 8B) sends both parallel calls in
one chunk, 3/3, and Bifrost's Anthropic and Responses streams kept one of two
(3/3 live, every run on a deterministic upstream); `/v1/chat/completions` kept
both. Real Claude Code over a deterministic upstream ran one of two Bash calls and
reported success. Present since 2025-10-15 and in `v2.3.0`.

Failed criterion 4: open third-party PR
[#7235](https://github.com/maximhq/bifrost/pull/7235) (2026-09-17, unreviewed)
fixes exactly this. It would be `duplicate-open`, so it was set aside in favour
of a novel lead.

## Lead B, filed as 103: the namespace of a searched-in tool call is dropped

Claude Code with `ENABLE_TOOL_SEARCH=true`, an MCP server, Bifrost `dev`, and
`openai/gpt-5.4-mini` halted with OpenAI `400 Missing namespace for
function_call` on the turn after its first MCP call. All four criteria pass; see
the issue writeup.

## Expectations (locked before the evidence run)

- E1: `dev`, `/anthropic/v1/messages`, a replayed Claude Code tool-search history
  ending in a call to the loaded tool: OpenAI 400 `Missing namespace`, N of N,
  buffered and streamed. The forwarded `function_call` has no `namespace`.
- E2: Bifrost's exact forwarded body sent straight to OpenAI gets the same 400,
  and the same body with only `namespace` added is served, N of N.
- E3: the same history with the tool eager (no search) is served on the same
  route, N of N.
- E4: Anthropic's server-side tool search with turn 1 generated live fails on
  turn 2 the same way, N of N.
- E5: Claude Code with tool search on runs the MCP tool once and then exits with
  the 400, N of N; `--continue` fails the same way; with tool search off it
  answers, N of N.
- E6: `v2.3.0` fails the deferred request earlier with the #8361 error, so the
  namespace defect is new on `dev`.
- E7: a kairo checker fails on Bifrost's forwarded bodies and passes on the bodies
  OpenAI served, and `cargo test --workspace`, fmt, clippy, and the README count
  check pass.

## Results

All seven held, N = 5 per cell (2 for `gpt-5.5`). Recorded in
`transcripts/103/capture/results.json`.
