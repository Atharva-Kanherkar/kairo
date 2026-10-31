# 114 transcripts

Sanitized wire evidence for LiteLLM silently dropping `max_parallel_requests` on
team creation and updates, completely disabling team-level concurrency rate
limits.

- Target: LiteLLM `v1.106.6`, commit `cfe0100e1fc` (2026-10-31)
- Client: `httpx` 0.28.1, Python 3.12.13, macOS arm64
- Database: PostgreSQL with Prisma client
- Reproduction date: 2026-10-31

## Layout

| Path | Contents |
|---|---|
| `rig/reproduce.py` | Starts the LiteLLM proxy harness, creates and updates a team with `max_parallel_requests`, and tests concurrency limiting on team keys vs control keys |
| `wire/team-new-request.json` | Request payload for `POST /team/new` specifying `max_parallel_requests: 1` |
| `wire/team-new-response.json` | Response payload from `POST /team/new` showing `max_parallel_requests: null` |
| `wire/team-update-request.json` | Request payload for `POST /team/update` specifying `max_parallel_requests: 2` |
| `wire/team-update-response.json` | Response payload from `POST /team/update` showing `max_parallel_requests: null` |
| `wire/team-info-response.json` | Response payload from `GET /team/info` showing `max_parallel_requests: null` |
| `wire/control-key-429-response.json` | Control key HTTP 429 response showing `rate_limit_type: max_parallel_requests` |

## Results

| Step | Endpoint | Sent `max_parallel_requests` | Observed `max_parallel_requests` | Status |
|---|---|---|---|---|
| Team creation | `POST /team/new` | 1 | null | 200 OK (dropped) |
| Team update | `POST /team/update` | 2 | null | 200 OK (dropped) |
| Team info | `GET /team/info` | (query) | null | 200 OK (absent) |

### Concurrency burst comparison

| Credential | Configured limit | 5 concurrent requests | Result |
|---|---|---|---|
| Control Virtual Key | `max_parallel_requests: 1` | [200, 429, 429, 429, 429] | 1 admitted, 4 throttled (enforced) |
| Team Key | `team.max_parallel_requests: 1` | [200, 200, 200, 200, 200] | 5 admitted, 0 throttled (bypassed) |

## Reproduce

```bash
DATABASE_URL="postgresql://<user>@localhost:5432/litellm_kairo" python transcripts/114/rig/reproduce.py
```
