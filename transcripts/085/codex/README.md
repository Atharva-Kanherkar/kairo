# 085 Codex consumer-boundary evidence

Run on 2026-09-24 UTC with the real Codex CLI 0.156.1 (`npm install @openai/codex@0.156.1`) against LiteLLM Proxy 1.102.1 (PyPI) and unreleased main at `8bbe7edb` (reports `1.104.0`).

## Rig

```text
Codex -> capture relay -> LiteLLM proxy (fallbacks) -> deterministic upstream
                                                    -> api.openai.com   (live case, fallback only)
```

- `run_codex_matrix.py` starts every process, runs `codex exec` five times per case, and writes one JSONL line per run.
- The deterministic primary streams one completed `exec_command` call, `echo run >> ledger.txt`, then a retriable in-band `error` event. Any request that already carries a tool result gets a final assistant message, which ends the Codex turn.
- Each run gets a fresh temporary `HOME`, `CODEX_HOME`, and working directory. Codex uses a custom `responses` provider pointed at the relay, `approval_policy = "never"`, the `workspace-write` sandbox, and `request_max_retries = 0` / `stream_max_retries = 0`. A repeated execution therefore cannot come from Codex retrying.
- The relay streams each exchange through unchanged, except that it swaps Codex's placeholder bearer for the synthetic proxy key.
- The number of lines in `ledger.txt` after `codex exec` exits is the number of times the side effect ran.
- Codex prints `Model metadata for <case> not found` for every case, because the cases are custom model names. It appears in the controls too and does not change the result.

## Reproduce

```sh
uv venv /tmp/kairo-085-litellm-1102 --python 3.12
uv pip install --python /tmp/kairo-085-litellm-1102/bin/python -r transcripts/085/requirements-1.102.1.txt
npm install --prefix /tmp/kairo-085-codex @openai/codex@0.156.1
python3 transcripts/085/codex/run_codex_matrix.py \
  --python /tmp/kairo-085-litellm-1102/bin/python \
  --codex /tmp/kairo-085-codex/node_modules/.bin/codex \
  --expect-litellm 1.102.1 --expect-codex 0.156.1 \
  --captured-at 2026-09-24 --output-dir /tmp/kairo-085-codex-out
python3 transcripts/085/codex/verify_refs.py /tmp/kairo-085-codex-out --shared /tmp/kairo-085-codex-out/shared
```

The live case adds `--live-fallback-model gpt-5.4-nano` and needs `OPENAI_API_KEY` in the environment. The key is read by the LiteLLM process only, is never passed to Codex, and the run fails if its value appears in any capture.

## Results

| Directory | Case | Fallback | Codex runs the side effect | Codex exit |
|---|---|---|---|---|
| `1.102.1`, `1.104.0` | `codex-trigger-duplicate-tool` | deterministic | **twice**, 5/5 each version | 0 |
| `1.102.1`, `1.104.0` | `codex-control-no-fault` | not used | once, 5/5 each version | 0 |
| `1.102.1`, `1.104.0` | `codex-control-fault-before-output` | deterministic | once, 5/5 each version | 0 |
| `1.102.1-live` | `codex-trigger-live-fallback` | live `gpt-5.4-nano` | **twice**, 5/5 | 0 |

In every trigger run, Codex's second request returns two tool results to the model, and nothing reports an error to the user. In the live case, the first turn carries two `response.created` events and `output_index` 0 is reused for a different item type. The primary's `function_call` sits at index 0, then the live model's commentary `message` at index 0, then its `function_call` at index 1. Codex runs the primary's `echo run >> ledger.txt` and then the live model's own `printf` command.

## What is committed

- `<dir>/<case>.jsonl`, one line per run:
  - `client_exchanges`: every Codex request and the exact SSE LiteLLM returned.
  - `upstream_exchanges`: every request LiteLLM forwarded to the deterministic upstream, with its response.
  - `codex_events`: Codex's `--json` event stream.
  - `ledger_lines` and `command_executions`: the measured side effect.
- `<dir>/metadata.json`: package versions, the `litellm/router.py` sha256, the Codex version, and the date.
- `shared/`: the exact raw bytes of Codex's top-level `instructions` and `tools` values. They are identical in every request, so each request body carries a `[kairo-ref sha256:... file:shared/...]` marker in their place, together with `request_body_sha256` of the full body. `verify_refs.py` rebuilds all 160 committed request bodies and checks each one byte for byte.
- Temporary directory paths are replaced inline with `[REDACTED:tmpdir]`, and any `encrypted_content` value with `[REDACTED:encrypted_content]`. No redaction touches lifecycle events, item ids, output indexes, or tool calls.
- The live fallback hop, LiteLLM to `api.openai.com`, is not captured. Its effect is visible in the client stream.
