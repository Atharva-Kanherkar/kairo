# transcripts/111

Evidence for finding 111: LiteLLM serves a virtual key a model outside its
allowlist when the denied model is named in the `generateContent` or
image-deployment URL path, because auth reads the body model and the handler
routes on the path model.

## Layout

- `rig/fake_upstream.py` - deterministic OpenAI-compatible upstream. Echoes the
  model in each forwarded request body (so the served model is visible), handles
  chat, streaming (SSE), responses and images, and writes each forwarded request
  and response to a scratch dir. Redacts `Authorization` / `x-goog-api-key`.
- `rig/config.yaml` - proxy config: `cheap-model` and `premium-model`, same local
  upstream, different upstream id and price. Master key and database url come from
  the environment; no secret is committed.
- `rig/drive.py` - runs the control ladder N = 5 per cell against the proxy,
  saving the client request/response and the matched forwarded upstream bytes per
  run, then writes `results.json`.
- `rig/run.sh` - boots the fake upstream and a LiteLLM proxy from a pinned source
  tree and runs the matrix. Generates the `cheap-model`-only key, dumps the spend
  log itself (needs `psycopg2` in the venv), and with `KR111_BUDGET_CAPTURE=1`
  runs the `$1` budget-drain stage.
- `rig/pr41852-auth_utils.diff` - the open upstream fix PR #41852 (auth_utils.py
  only), used for the fix-control build. On `cfe0100e` apply it with `patch -p1`
  (plain `git apply` rejects the offsets; `git apply --3way` also works).
- `wire/` - stock `main` (`cfe0100e`). `wire-pr41852/` - `main` with the fix PR
  applied. Each has `results.json` and one directory per cell with `run-01`..`run-05`,
  holding `client-request.http`, `client-response.http`, and
  `upstream/fwd-request.http` + `upstream/fwd-response.http`.
- `wire/spend.json` - raw `LiteLLM_SpendLogs` rows for the `main` matrix with the
  key token hash and status: `premium-model` billed `$0.2` on the generateContent,
  streamGenerateContent and chat sibling cells, `$0` on the image cells
  (`cost_calculation_error`), `$0` failure rows for the 403 controls, all on the
  single `cheap-only-111` key hash.
- `wire/budget/` - the `$1` budget-drain stage: `resp-1..6.json` and `status-N.txt`
  (five `200` served `premium-model`, the sixth `422 budget_exceeded`), plus
  `spend.json`, the six raw spend rows for the `cheap-only-budget1` key.

## Reproduce

```sh
# from a litellm checkout at cfe0100e, editable-installed into <venv>
# the venv also needs psycopg2 so run.sh dumps the spend log:
#   uv pip install --python <venv>/python psycopg2-binary
export KR111_MASTER_KEY=sk-...            # any master key
export KR111_DATABASE_URL=postgresql://... # a throwaway Postgres
KR111_BUDGET_CAPTURE=1 PROXY_PORT=4028 UPSTREAM_PORT=4190 \
  bash rig/run.sh <litellm_src> <venv_bin> "$PWD/wire" main

# fix-control: apply the open fix PR to <litellm_src> first, then re-run
( cd <litellm_src> && patch -p1 < <this_dir>/rig/pr41852-auth_utils.diff )
PROXY_PORT=4028 UPSTREAM_PORT=4190 \
  bash rig/run.sh <litellm_src> <venv_bin> "$PWD/wire-pr41852" main+pr41852
( cd <litellm_src> && git checkout -- litellm/proxy/auth/auth_utils.py \
    && rm -f litellm/proxy/auth/auth_utils.py.orig )
```

Credentials are passed by environment variable name only and never committed. The
`Authorization` and `x-goog-api-key` header values in every captured request are
replaced with `[REDACTED]` inline.
