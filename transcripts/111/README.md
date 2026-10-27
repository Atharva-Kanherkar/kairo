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
  tree and runs the matrix. Generates the `cheap-model`-only key.
- `rig/pr41852-auth_utils.diff` - the open upstream fix PR #41852 (auth_utils.py
  only), used for the fix-control build.
- `wire/` - stock `main` (`cfe0100e`). `wire-pr41852/` - `main` with the fix PR
  applied. Each has `results.json` and one directory per cell with `run-01`..`run-05`,
  holding `client-request.http`, `client-response.http`, and
  `upstream/fwd-request.http` + `upstream/fwd-response.http`.
- `wire/spend.json` - spend-log summary for the `main` run: all spend on key alias
  `cheap-only-111`, `premium-model` billed against the `cheap-model`-scoped key.

## Reproduce

```sh
# from a litellm checkout at cfe0100e, editable-installed into <venv>
export KR111_MASTER_KEY=sk-...            # any master key
export KR111_DATABASE_URL=postgresql://... # a throwaway Postgres
PROXY_PORT=4028 UPSTREAM_PORT=4190 \
  bash rig/run.sh <litellm_src> <venv_bin> "$PWD/wire" main

# fix-control: apply rig/pr41852-auth_utils.diff to <litellm_src> first, then
PROXY_PORT=4028 UPSTREAM_PORT=4190 \
  bash rig/run.sh <litellm_src> <venv_bin> "$PWD/wire-pr41852" main+pr41852
```

Credentials are passed by environment variable name only and never committed. The
`Authorization` and `x-goog-api-key` header values in every captured request are
replaced with `[REDACTED]` inline.
