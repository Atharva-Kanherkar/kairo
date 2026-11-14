# 115 transcripts

Sanitized wire evidence for LiteLLM virtual keys with empty `models: []`
and enterprise access group model permissions (falsified finding / negative result).

- Target: LiteLLM `v1.106.6`, commit `cfe0100e1fc` (2026-10-31)
- Client: `httpx` 0.28.1, Python 3.12.13, macOS arm64
- Database: PostgreSQL with Prisma client
- Upstream server: deterministic local mock OpenAI chat completions server
- Reproduction date: 2026-10-31

## Layout

| Path | Contents |
|---|---|
| `rig/reproduce.py` | Starts mock server and LiteLLM proxy harness, creates an access group restricted to `allowed-model`, creates trigger, sentinel, and control keys, and exercises chat completions |
| `wire/access-group-create-request.json` | Request payload for `POST /v1/access_group` restricting `access_model_names` to `["allowed-model"]` |
| `wire/access-group-create-response.json` | Response payload from `POST /v1/access_group` returning the created `access_group_id` |
| `wire/key-generate-request.json` | Request payload for `POST /key/generate` specifying `models: []` and `access_group_ids` |
| `wire/key-generate-response.json` | Response payload from `POST /key/generate` confirming trigger key creation |
| `wire/nodefault-key-generate-request.json` | Request payload for `POST /key/generate` specifying `models: ["no-default-models"]` and `access_group_ids` |
| `wire/nodefault-key-generate-response.json` | Response payload from `POST /key/generate` confirming sentinel key creation |
| `wire/control-key-forbidden-response.json` | Response to control key (`models: ["allowed-model"]`) requesting `forbidden-model`: HTTP 403 `key_model_access_denied` |
| `wire/nodefault-key-forbidden-response.json` | Response to sentinel key (`models: ["no-default-models"]`) requesting `forbidden-model`: HTTP 403 `key_model_access_denied` |
| `wire/nodefault-key-allowed-response.json` | Response to sentinel key requesting `allowed-model`: HTTP 200 OK |
| `wire/trigger-key-forbidden-request.json` | Request from trigger key requesting `forbidden-model` |
| `wire/trigger-key-forbidden-response.json` | Response to trigger key requesting `forbidden-model`: HTTP 200 OK (empty models allows all models by design) |
| `wire/trigger-key-allowed-request.json` | Request from trigger key requesting `allowed-model` |
| `wire/trigger-key-allowed-response.json` | Response to trigger key requesting `allowed-model`: HTTP 200 OK |
| `wire/results.json` | 5 of 5 runs summary with target commit and run status codes |

## Results

Across 5 of 5 identical runs:

| Step | Key configuration | Model requested | Expected status | Observed status | Result |
|---|---|---|---|---|---|
| Control Key | `models: ["allowed-model"]` | `forbidden-model` | 403 Forbidden | 403 Forbidden | Enforced |
| Sentinel Key | `models: ["no-default-models"]`, `access_group: ["allowed-model"]` | `forbidden-model` | 403 Forbidden | 403 Forbidden | Enforced |
| Sentinel Key | `models: ["no-default-models"]`, `access_group: ["allowed-model"]` | `allowed-model` | 200 OK | 200 OK | Served |
| Trigger Key | `models: []`, `access_group: ["allowed-model"]` | `forbidden-model` | 200 OK | 200 OK | Allowed by design |
| Trigger Key | `models: []`, `access_group: ["allowed-model"]` | `allowed-model` | 200 OK | 200 OK | Served |

## Reproduce

```bash
DATABASE_URL="postgresql://<user>@localhost:5432/litellm_kairo" python transcripts/115/rig/reproduce.py
```
