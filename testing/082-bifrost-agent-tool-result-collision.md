# 082 Bifrost same-name agent tool results: test contract

## Functional behavior

- Pin and build Bifrost `dev` commit
  `1a17949c` from source, record its component version, and exercise the shipped
  `bifrost-http` public entry point rather than only an internal Go function.
- Configure documented MCP agent mode with a deterministic local
  OpenAI-compatible upstream and a streamable HTTP MCP server. No provider
  credential is required.
- Send a non-streaming `POST /v1/chat/completions` turn in which the upstream
  emits two auto-executable calls with distinct `tool_call_id` values and the
  same tool name, plus one non-auto-executable call that forces Bifrost to
  return the mixed turn to the client.
- Require the MCP server to observe both auto-executable calls with their
  distinct arguments and require the client-visible response to preserve one
  result for each executed `tool_call_id`.
- Run the violation five times. Record survivor identity without averaging it
  away because parallel tool completion may change which result is retained.
- Run a distinct-name control five times with otherwise equivalent calls. Both
  executed results must be returned.
- Run a single-auto-call control five times. Its one executed result must be
  returned.
- Feed the violation and distinct-name control into the same deterministic
  next-turn policy. If an executed side effect lacks a result, the policy
  requests that operation again. Record whether duplicate execution occurs.
- Store sanitized literal client requests and responses, forwarded upstream
  requests and responses, and MCP execution records under `transcripts/082/`.
  Use synthetic values only.
- Search current Bifrost issues, pull requests, commits, releases, changelog,
  documentation, tests, examples, UI, and clients on the day of filing. Record
  exact search terms and direct links.

## Unit tests

- Add or reuse an invariant-level checker that compares the set of executed MCP
  call IDs with the set of tool-result call IDs reported to the client.
- The violating fixture must produce `Violation` because one executed call ID
  has no reported result.
- The distinct-name and single-call fixtures must produce `Conformant`.
- Add a vacuity guard proving the checker consults call IDs rather than only
  comparing aggregate counts or tool names.
- Assert the pinned commit, route, run counts, and consumer replay counts from
  `transcripts/082/results.json`.

## Integration / functional tests

- The reproduction script must build or validate the pinned Bifrost binary,
  start the real gateway, deterministic upstream, and streamable HTTP MCP
  server, then execute all three cells.
- The violation must show two distinct MCP executions and one client-visible
  executed result in 5/5 runs.
- The distinct-name control must show two executions and two results in 5/5
  runs.
- The single-call control must show one execution and one result in 5/5 runs.
- The consumer replay must duplicate the missing side effect in the violation
  cell and must not duplicate either side effect in the distinct-name control.
- A patched or non-reproducing target must make the violation assertion fail
  loudly instead of silently producing conformant evidence.

## Smoke tests

- `cargo test --workspace`
- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets -- -D warnings`
- `python3 tools/update-readme-counts.py --check`
- Scan the complete diff and transcript tree for credentials, private prompts,
  temporary runtime state, and unrelated files.

## E2E tests

- Run the complete reproduction from a clean Bifrost checkout at the pinned
  commit through `POST /v1/chat/completions`.
- Run the documented deterministic consumer replay from the frozen first-turn
  evidence.
- A live provider confirmation is not required unless the local upstream could
  plausibly create the result-association defect. Record that decision in the
  writeup.

## Manual / cURL tests

```text
BIFROST_SOURCE=/path/to/bifrost \
BIFROST_BIN=/path/to/bifrost-http \
python3 transcripts/082/reproduce.py

cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
python3 tools/update-readme-counts.py --check
```

## Review decision

- Apply all three gates and every bug-or-not question in `AGENTS.md`.
- The author may record `ACCEPT` only if the exact runtime claim, consumer
  consequence, current upstream status, invariant coverage, and repository
  checks all pass.
- The author's self-review does not satisfy the required independent review.
  Until `.github/agents/kairo-reproduction-reviewer.agent.md` reruns the
  critical path read-only, the pull request must state that independent review
  is pending.
