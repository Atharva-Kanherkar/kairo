# 098 transcript: LiteLLM image routes let a scoped virtual key reach a model named in the URL

Finding writeup:
[`../../issues/098-litellm-image-model-allowlist-bypass/README.md`](../../issues/098-litellm-image-model-allowlist-bypass/README.md)

## What is recorded here

A real LiteLLM proxy, started from the pinned checkout, with a real
PostgreSQL-backed virtual key scoped to `models: ["allowed-model"]`. Two
deterministic OpenAI-compatible upstreams sit on loopback ports, one for
`allowed-model` and one for `denied-model`, and record every forwarded request
in a JSONL ledger. The script sends 11 cases five times each and writes the raw
client request, client response, and the per-case upstream ledger for every
attempt.

The defect is in the proxy's own auth and routing, not in the upstream. The
upstreams return fixed markers so that a response proves which deployment
produced it:

- `SERVED_BY_ALLOWED_IMAGE` and `https://example.invalid/ALLOWED.png`
- `SERVED_BY_DENIED_IMAGE` and `https://example.invalid/DENIED.png`

## Environment

- macOS arm64 (Apple Silicon), Python 3.12.13.
- LiteLLM `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a`, `pyproject.toml`
  version 1.108.0 (2026-10-26), the `main` HEAD on 2026-10-27.
- PostgreSQL database `litellm_kairo`, already migrated with the LiteLLM
  schema. The key is created at run time with `POST /key/generate` and
  `{"models": ["allowed-model"]}`.
- One uvicorn worker, `drop_params: true`, `LITELLM_LOCAL_MODEL_COST_MAP=True`.
- Prisma query engine 5.4.2
  (`query-engine-darwin-arm64`). The 5.17.0 engine fails raw-query startup on
  this checkout.
- No provider credential is used. The upstream `api_key` is the literal
  `sk-dummy-upstream` in the generated `config.yaml`.

The proxy reports `x-litellm-version: 1.104.0` because the installed package
metadata is stale. The tested identity is the commit above and the
`pyproject.toml` version, not that header.

## How to rerun

```sh
cd /path/to/litellm-main    # cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a
PYTHONPATH=/path/to/litellm-main \
DATABASE_URL=postgresql://user@127.0.0.1:5432/litellm_kairo \
PRISMA_QUERY_ENGINE_BINARY=/path/to/query-engine-darwin-arm64 \
/path/to/venv/bin/python transcripts/098/reproduce.py \
  --litellm-source /path/to/litellm-main
```

`reproduce.py` writes `config.yaml`, starts the upstreams and the proxy, creates
the virtual key, then writes `runs/run-01..05/` and `summary.json`. It exits
non-zero if any case does not match its expected status, served marker, and
upstream ledger. The database must already have the schema; the script does not
create it.

## Cases

| Case | Request | Expected | Observed |
|---|---|---|---|
| `gen_body_denied` | `/v1/images/generations`, body `denied-model` | 403, no upstream | 403, no upstream |
| `gen_no_query` | `/v1/images/generations`, body `allowed-model` | 200, allowed upstream | 200, allowed |
| `gen_query_allowed` | `?model=allowed-model`, body `allowed-model` | 200, allowed upstream | 200, allowed |
| `gen_query_denied` | `?model=denied-model`, body `allowed-model` | 200, denied upstream | 200, denied |
| `gen_deploy_denied` | `/openai/deployments/denied-model/images/generations`, body `allowed-model` | 200, denied upstream | 200, denied |
| `gen_query_denied_nobody` | `?model=denied-model`, no body model | 403, no upstream | 403, no upstream |
| `gen_deploy_denied_nobody` | `/openai/deployments/denied-model/images/generations`, no body model | 403, no upstream | 403, no upstream |
| `edit_body_denied` | `/v1/images/edits`, multipart `model=denied-model` | 403, no upstream | 403, no upstream |
| `edit_query_denied` | `?model=denied-model`, multipart `model=allowed-model` | 200, denied upstream | 200, denied |
| `edit_deploy_denied` | `/openai/deployments/denied-model/images/edits`, multipart `model=allowed-model` | 200, denied upstream | 200, denied |
| `edit_no_query` | `/v1/images/edits`, multipart `model=allowed-model` | 200, allowed upstream | 200, allowed |

Every row is 5/5. `summary.json` is the machine-readable form.

## Layout

```text
transcripts/098/
  reproduce.py                  the rig
  upstream.py                   deterministic OpenAI-compatible upstream
  config.yaml                   generated model list, one deployment per port
  key-metadata.json             the key's models list and alias
  summary.json                  commit, time, and 5/5 counts per case
  upstream-allowed.jsonl        full-session allowed-upstream ledger
  upstream-denied.jsonl         full-session denied-upstream ledger
  runs/run-01..05/<case>/
    client-request.http         raw request bytes, virtual key redacted
    client-response.http        raw response bytes
    upstream-allowed.jsonl      per-case slice of the allowed ledger
    upstream-denied.jsonl       per-case slice of the denied ledger
    result.json                 expected and observed status, marker, calls
```

The per-case ledgers are slices taken between byte offsets in the full-session
ledgers, so the two files together show every upstream call the run made. The
`runs/` directory is deleted and rewritten on each run.

## Redactions

- `Authorization: Bearer <VIRTUAL_KEY>` in every client request. The key value
  is generated per run and never written.
- The 68-byte PNG in the multipart client request is replaced with
  `<TINY_PNG 68 bytes>` so the capture stays text. The full multipart body,
  including the image bytes, is preserved in the upstream ledger's `body_b64`,
  which is the forwarded byte evidence under test.
- No other bytes are redacted. The `model` fields, the query strings, the
  deployment paths, the status lines, and the response headers are verbatim.

## Why the upstream is a stub

The claim is about which deployment the proxy chooses and whether the key's
allowlist admits it. The stub does not make that decision; it only records the
request and returns a fixed marker. The proxy, its auth, its router and the
Postgres key store are the real pinned implementation. The response headers
(`x-litellm-model-api-base`, `x-litellm-model-group`) and the upstream ledger
independently show the same deployment, so a stub that merely echoed the body
could not produce this evidence.
