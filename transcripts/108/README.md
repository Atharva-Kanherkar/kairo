# 108 transcripts

Sanitized wire evidence for the LiteLLM pass-through cross-origin redirect
credential leak.

- Target: LiteLLM `v1.106.2`,
  `49943cd52ea46aacf8db2e5cb2b890d580fe9bb4` (2026-10-21)
- Client: `httpx` 0.28.1, Python 3.11.9, macOS arm64
- Reproduction date: 2026-10-22
- Credentials: fixed synthetic canaries only. Every recorded file replaces them
  with `[CANARY_PROVIDER_KEY]`, `[CANARY_MASTER_KEY]`, or
  `[CANARY_SALT_KEY]`

## Layout

| Path | Contents |
|---|---|
| `config.yaml` | LiteLLM proxy config for the `mock` model. No real credential |
| `reproduce.py` | Starts two real LiteLLM proxies, the 307 origin, the direct origin, and the sink, then sends five requests to each proxy |
| `trigger/run-01..05/` | The configured origin answers 307 to the sink |
| `control/run-01..05/` | The configured origin answers 200 directly |

Each run directory contains:

- `client-request.http`: the caller's request to LiteLLM
- `client-response.http`: LiteLLM's response to the caller
- `upstream-origin-01.http`: the request LiteLLM sent to the configured origin
- `upstream-sink-01.http`: the request the redirect target received (trigger only)
- `origin.jsonl`: the configured origin request as a machine-readable header map
- `sink.jsonl`: the redirect target request as a machine-readable header map
  (empty in every control run)
- `result.json`: the per-run booleans used by the conformance suite

The proxy logs are intentionally absent. They may contain the synthetic
canaries, so the runner writes them only to its output directory.

## Result

| Case | Client 200 | Configured origin hit | Sink hit | Origin `api-key` | Origin `authorization` | Sink `api-key` | Sink `authorization` |
|---|---|---|---|---|---|---|---|
| Trigger, 5 runs | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 0/5 |
| Control, 5 runs | 5/5 | 5/5 | 0/5 | 5/5 | 5/5 | 0/5 | 0/5 |

`api-key` and `authorization` carry the same synthetic provider credential.
`httpx` 0.28.1 strips `authorization` on the cross-origin hop and forwards
`api-key`.

## Reproduce

```text
uv venv --python 3.11 /tmp/kairo-108-venv
uv pip install --python /tmp/kairo-108-venv/bin/python 'litellm[proxy]==1.106.2'
/tmp/kairo-108-venv/bin/python transcripts/108/reproduce.py \
  --litellm-bin /tmp/kairo-108-venv/bin/litellm \
  --output-dir /tmp/kairo-108-review
```

The runner binds loopback servers on ephemeral ports and starts two proxies on
ephemeral ports. It prints one JSON result per run. The committed directories
are a copy of such a run.
