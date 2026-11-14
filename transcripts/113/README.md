# transcripts/113

Evidence for finding 113: LiteLLM serves a virtual key a model outside its
allowlist on the `/gemini/{endpoint:path}` pass-through route when targeting Google
General Availability (`/v1/`) and experimental (`/v1alpha/`) endpoints, because
auth's `get_model_from_request` uses a regex restricted to `/(?:v1beta|beta)/models/`
and fails to resolve the model name.

## Layout

- `rig/fake_upstream.py`: deterministic Google AI Studio-compatible upstream.
  Echoes the model extracted from the forwarded URL path, handles non-streaming
  and streaming (SSE), and writes each forwarded request and response sequentially
  to a scratch dir. Redacts secrets.
- `rig/config.yaml`: proxy config with two registered models (`cheap-model` and
  `premium-model`). Master key and database url come from the environment; no secret
  is committed.
- `rig/drive.py`: runs the differential control ladder N = 5 per cell against the
  proxy, saving the client request/response and matched forwarded upstream bytes
  per run, then writes `results.json`.
- `rig/run.sh`: boots the fake upstream and a LiteLLM proxy from the pinned
  environment, generates the `cheap-model`-only key, and runs the test suite.
- `wire/`: raw HTTP exchanges for each cell (`run-01` to `run-05`):
  - `gemini-v1-path-denied`: `POST /gemini/v1/models/premium-model:generateContent`
    (TRIGGER: 200 OK, forwarded to `premium-model`)
  - `gemini-v1-stream-path-denied`: `POST /gemini/v1/models/premium-model:streamGenerateContent`
    (TRIGGER: 200 OK, forwarded to `premium-model`)
  - `gemini-v1alpha-path-denied`: `POST /gemini/v1alpha/models/premium-model:generateContent`
    (TRIGGER: 200 OK, forwarded to `premium-model`)
  - `gemini-models-path-denied`: `POST /gemini/models/premium-model:generateContent`
    (TRIGGER: 200 OK, forwarded to `premium-model`)
  - `gemini-v1beta-control`: `POST /gemini/v1beta/models/premium-model:generateContent`
    (CONTROL: 403 Forbidden, `key_model_access_denied`)
  - `gemini-v1-cheap-control`: `POST /gemini/v1/models/cheap-model:generateContent`
    (CONTROL: 200 OK, forwarded to `cheap-model`)
- `wire/results.json`: summary matrix of all runs and outcomes.
- `wire/proxy.out.txt`: proxy stdout log.

## Reproduce

```sh
export KR113_MASTER_KEY=sk-master-113
export KR113_DATABASE_URL=postgresql://atharva@localhost:5432/litellm_kairo
cd transcripts/113 && bash rig/run.sh /Users/atharva/.cache/kr-hunt-1027/venv/bin "$PWD/wire" main
```

Credentials are passed by environment variable name only and never committed. The
`key` parameter in every captured client request is marked `[REDACTED]`.
