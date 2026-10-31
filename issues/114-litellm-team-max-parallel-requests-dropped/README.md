# 114, LiteLLM drops team `max_parallel_requests` on creation and update and never enforces team concurrency

- **Upstream**: [BerriAI/litellm#49070](https://github.com/BerriAI/litellm/issues/49070), open upstream issue; unmerged PR [BerriAI/litellm#41828](https://github.com/BerriAI/litellm/pull/41828)
- **Tool under test**: LiteLLM `v1.106.6`, commit `cfe0100e1fc` (2026-10-31)
- **Reproduced**: 2026-10-31, local PostgreSQL database, pinned checkout commit `cfe0100e1fc`, Python 3.12.13, macOS arm64

## What breaks

When a proxy operator configures a concurrency limit on a team (`max_parallel_requests: 1`) via `POST /team/new` or `POST /team/update`, LiteLLM silently drops the field and responds with `"max_parallel_requests": null`. The value is never saved to `LiteLLM_TeamTable.max_parallel_requests` in the database.

Furthermore, even if `max_parallel_requests` is manually written to the database column, `parallel_request_limiter_v3.py` omits `max_parallel_requests` from the team rate limit descriptors entirely, and `UserAPIKeyAuth` never loads or propagates `team_max_parallel_requests`. As a consequence, concurrent requests made using keys belonging to that team are never throttled at the team boundary. A burst of 5 concurrent requests from a team key all return HTTP 200, whereas an identical control key with a key-level limit of 1 correctly throttles 4 of the 5 requests with HTTP 429 (`rate_limit_type: max_parallel_requests`).

This breaks multi-tenant isolation: teams intended to have concurrency caps can issue unbounded simultaneous requests, starving backend model quotas and overwhelming upstream providers.

### Bug or not

- Is the expected behavior really the spec? Yes. Official LiteLLM documentation at `https://docs.litellm.ai/docs/proxy/users` explicitly documents:
  `max_parallel_requests: Optional[int] - Rate limit a team based on the number of parallel requests. Raises 429 error, if team's parallel requests > x.`
  In addition, `schema.prisma` defines `max_parallel_requests Int?` directly on `LiteLLM_TeamTable`.
- Have maintainers already ruled on it? Yes. Issue #49070 was reported as a bug, and draft PR #41828 was submitted to wire up team concurrency enforcement across the API, auth, and limiter layers.
- Is the trigger supported usage? Yes. Passing `max_parallel_requests` to `POST /team/new` or `POST /team/update` is standard documented usage.
- Is a real boundary crossed? Yes. A team configured with a concurrency cap of 1 bypasses that restriction completely and runs arbitrary concurrent requests without receiving HTTP 429.
- What fix would a maintainer ship? Add `max_parallel_requests` to `NewTeamRequest` and `UpdateTeamRequest`, load it into `UserAPIKeyAuth`, and build team-level `max_parallel_requests` descriptors in `parallel_request_limiter_v3.py`.
- Label: `bug`.

## Wire evidence

- `transcripts/114/wire/team-new-request.json`: client request sending `max_parallel_requests: 1`
- `transcripts/114/wire/team-new-response.json`: proxy response returning `"max_parallel_requests": null`
- `transcripts/114/wire/team-update-request.json`: client request sending `max_parallel_requests: 2`
- `transcripts/114/wire/team-update-response.json`: proxy response returning `"max_parallel_requests": null`
- `transcripts/114/wire/team-info-response.json`: proxy response returning `"max_parallel_requests": null`
- `transcripts/114/wire/control-key-429-response.json`: control key HTTP 429 response when key-level limit is 1

## Differential controls

| Credential | Configured limit | 5-request burst statuses | Observed behavior |
|---|---|---|---|
| Control Virtual Key | `max_parallel_requests: 1` | `[200, 429, 429, 429, 429]` | 1 admitted, 4 rejected with HTTP 429 |
| Team Virtual Key | `team.max_parallel_requests: 1` | `[200, 200, 200, 200, 200]` | 5 admitted, 0 rejected (100% bypass) |

Reproduction rate: 5 of 5 runs reproducible.

## Root cause

Two distinct root causes combine to cause this defect:

1. **Schema omission**: In `litellm/proxy/_types.py`, `NewTeamRequest` and `UpdateTeamRequest` omit `max_parallel_requests` from their Pydantic field definitions. Because Pydantic ignores extra fields, `data.model_dump(exclude_unset=True)` drops `max_parallel_requests` before writing to the database or building the response.
2. **Limiter omission**: In `litellm/proxy/hooks/parallel_request_limiter_v3.py` lines 3378-3392, `create_rate_limit_descriptors` checks team rate limits for `team_rpm_limit` and `team_tpm_limit`, but completely omits `max_parallel_requests` from the team descriptor. Furthermore, `UserAPIKeyAuth` lacks a field to hold team-level concurrency limits.

## Upstream status

- Classification: `duplicate-open`
- Upstream issue: [BerriAI/litellm#49070](https://github.com/BerriAI/litellm/issues/49070)
- Upstream pull request: [BerriAI/litellm#41828](https://github.com/BerriAI/litellm/pull/41828)
- Date checked: 2026-10-31

## Test

Harness conformance test in `crates/harness/tests/conformance.rs`:
- `litellm_team_new_drops_max_parallel_requests_violation`: asserts that `POST /team/new` with `max_parallel_requests: 1` returning `null` is caught as a violation.
- `litellm_team_update_drops_max_parallel_requests_violation`: asserts that `POST /team/update` with `max_parallel_requests: 2` returning `null` is caught as a violation.
- `litellm_team_concurrency_preserved_control`: asserts that a hypothetical lossless response preserving the configured limit passes as conformant.
