# Transcripts for finding 112

Workers AI's `-external` backend for `@cf/google/gemma-4-26b-a4b-it` merges two
parallel tool calls into one unparseable call. Writeup:
[`issues/112-workers-ai-gemma4-parallel-toolcalls-merged`](../../issues/112-workers-ai-gemma4-parallel-toolcalls-merged/README.md).

## Service fingerprint

| Field | Value |
|---|---|
| Product | Cloudflare Workers AI (hosted, closed source) |
| Entry point | Workers `AI` binding, `env.AI.run(model, inputs, { returnRawResponse: true })`, from `wrangler dev` 4.139.0 (the AI binding always runs remotely) |
| Account | free plan, logged in with `wrangler login`; account id not recorded |
| UTC window | 2026-10-31T05:56:13Z to 2026-10-31T05:59:27Z (`matrix.md`) |
| Edge colo | NRT (`cf-ray` suffix) |
| Requested model | `@cf/google/gemma-4-26b-a4b-it` |
| Observed serving model | `@cf/google/gemma-4-26b-a4b-it` (native) or `@cf/google/gemma-4-26b-a4b-it-external`, from the response `model` field |
| Build or release id | none exposed; each response carries `cf-ai-req-id` and `cf-ai-neurons` |
| Routing | Workers AI picks the backend per request; no documented way to pin one. The consumer cell goes through the account's `default` AI Gateway, created by `createAI` on first use, no cache, no fallback |
| Client packages | `agents` 0.32.0 (`agents/models/ai-sdk`), `ai` 7.0.159, pinned in `rig/package-lock.json` |
| Direct-provider substitute | OpenRouter `google/gemma-4-26b-a4b-it`, provider PrimeIntellect, same window |
| Contract | [Gemma 4 model page](https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/) (function calling: Yes; `parallel_tool_calls` default true) and the [2026-04-04 changelog post](https://developers.cloudflare.com/changelog/post/2026-04-04-gemma-4-26b-a4b-workers-ai/), both read 2026-10-31 |

The hop between the binding and the serving backend is not observable. The
bytes in each `response.http` are what Workers AI returned to the binding.

## Layout

```text
rig/                    wrangler dev Worker: /raw (plain binding) and /agent (createAI agent loop)
reproduce.py            runs every cell and rebuilds matrix.md
matrix.md               per-cell, per-backend verdicts and the UTC window
cells/<cell>/run-NN/
  request.json          exact binding.run arguments (binding cells and agent cells)
  request.http          exact HTTP request (OpenRouter cells)
  response.http         status, headers, and body exactly as returned
  agent.json            agent cells only: steps, tool errors, executions, every binding.run call with its raw body
  meta.json             served model, client-side reconstruction of the tool calls, verdict
```

Cells:

| Cell | Rung | What it sends |
|---|---|---|
| `plain-parallel-nonstream` | plain path | two-city prompt, non-streaming, 10 runs |
| `plain-parallel-stream` | plain path | two-city prompt, streaming, 10 runs |
| `trigger-removed-single-nonstream` | trigger removed | one-city prompt, non-streaming, 5 runs |
| `trigger-removed-single-stream` | trigger removed | one-city prompt, streaming, 5 runs |
| `direct-openrouter-parallel-nonstream` | direct-provider substitute | two-city prompt on OpenRouter, 5 runs |
| `direct-openrouter-parallel-stream` | direct-provider substitute | two-city prompt on OpenRouter, streaming, 5 runs |
| `consumer-createai-agent` | routed path and consumer | AI SDK `generateText` agent on `createAI`, executing tool, 5-step limit, 6 runs |

## Redactions

- OpenRouter `request.http`: the `authorization` header value is replaced inline
  with `[REDACTED:OPENROUTER_API_KEY]`, and the `set-cookie` response header
  value (a Cloudflare bot-management cookie) with `[REDACTED:set-cookie]`.
- Agent cells: the AI Gateway `cf-aig-log-id`, `cf-aig-event-id`,
  `cf-aig-trace-id`, and `cf-aig-request-id` response header values are replaced
  inline with `[REDACTED:<header>]`, because they identify entries in the
  account's gateway logs.

Nothing else is changed. No bytes under dispute are redacted.

## Reproduce

```sh
cd rig && npm ci
npx wrangler login
npx wrangler dev --port 8799
# in another shell, from this directory
OPENROUTER_API_KEY=... python3 reproduce.py
python3 reproduce.py --summary   # rebuild matrix.md from saved cells only
```
