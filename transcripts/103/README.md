# 103 transcripts

Writeup: [`issues/103-bifrost-tool-search-namespace-dropped`](../../issues/103-bifrost-tool-search-namespace-dropped/README.md).

- `rig/`: [`reproduce.py`](rig/reproduce.py) (relays, cases, results),
  [`config.json`](rig/config.json) (the whole gateway config),
  [`mcp_server.py`](rig/mcp_server.py) (stdio MCP server with a call ledger),
  [`build-and-run.md`](rig/build-and-run.md) (builds, hashes, command line).
- `capture/results.json`: one row per case with client status, upstream statuses,
  whether the namespace rejection or the canary answer appeared, and for Claude Code
  the exit code, MCP executions and the `--continue` result.
- `capture/<build>/<group>/<case>/`: `client/NNN-request.http` and
  `NNN-response.http` are the client hop; `upstream/` is Bifrost to OpenAI. `direct/`
  cases have no gateway and keep the single exchange at the top of the case directory.
  Claude Code cases add `claude-stdout.jsonl`, `ledger.jsonl` and a `continue/`
  directory for the resumed session.

| Group | Case | What it is |
|---|---|---|
| `minimal` | `deferred-{nonstream,stream}-NN` | Claude Code's tool-search history, tool deferred |
| `minimal` | `eager-{nonstream,stream}-NN` | the same history, tool eager, no search (trigger removed) |
| `minimal` | `hosted-nonstream-NN` | Anthropic server-side `tool_search_tool_bm25`, turn 1 live, turn 2 replayed |
| `minimal` | `deferred-nonstream-gpt-5.5-NN` | the deferred case on `openai/gpt-5.5` |
| `responses` | `{with,without}-namespace-NN` | the same history in the Responses dialect on `/v1/responses` |
| `direct` | `{as-forwarded,namespace-restored}-NN` | Bifrost's forwarded body for `deferred-nonstream-01`, sent to OpenAI as is and with only `namespace` added |
| `claude` | `tool-search-{on,off}-NN` | Claude Code 2.1.289 with `ENABLE_TOOL_SEARCH=true` / `false` |

`capture-9bcea677/<model>/` is the model-scope rerun on the newer `dev` head
`9bcea6777488adc674dc8d326bd4933b71746a22`, same rig and cases, one directory per
model with its own `results.json`:

| Directory | Command |
|---|---|
| `gpt-6.1-sol/` | `--model openai/gpt-6.1-sol --runs 3 --extra-model openai/gpt-6-sol --extra-model openai/gpt-6-luna --extra-model openai/gpt-6-astra --claude dev-9bcea677` |
| `gpt-6-sol/`, `gpt-6-luna/`, `gpt-6-astra/` | `--model openai/<model> --runs 2 --groups minimal` |
| `gpt-5.4-mini/` | `--model openai/gpt-5.4-mini --runs 2 --groups minimal` (same-build control) |

Ten client response bodies on the `responses` route had provider headers echoed inside
the JSON (`Openai-Project`, `Cf-Ray`, rate limits). Those values were redacted after
recording with the same `redact_body` the relay now applies, so `Content-Length` in
those ten files describes the original body.
