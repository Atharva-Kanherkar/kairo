# 146, LiteLLM's MCP OAuth `POST /{mcp_server_name}/register` does not shadow `POST /guardrails/register`: false positive, withdrawn

**Verdict: false positive. Withdrawn after independent review (REJECT) and kept
as an honest negative. The claimed shadowing does not reproduce on the pinned
commit: anonymous callers get 401 and authenticated callers reach the real
guardrail handler, in either lazy-load order. See "Why this is withdrawn".
The original claim is kept below unchanged for the record.**

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). No upstream
  issue or PR covers it (searched 2026-11-14: "guardrails/register",
  "guardrails register route"). Nothing to file: the defect does not exist on
  the tested commit.
- **Recorded as**: originally `novel` functional regression; withdrawn, does
  not reproduce.
- **Tool under test**: LiteLLM at main `6abacd329b70dc668f69c705eb7118fbd639cd96`
  (upstream default branch, 2026-11-13), real proxy CLI. Python 3.12.13.
- **Reproduced**: original author run 2026-11-14, 3 of 3 probes, with no request
  bytes or build fingerprint. Independent rerun on the same commit: shadowed 0 of 26 (15 anonymous,
  11 master key).
- **Label**: none. The claim fails the Correctness gate, so the bug-or-not
  label does not apply (the original draft said `bug`).

## Why this is withdrawn

The independent review on 2026-11-14 reran the exact probes against a fresh
checkout of `6abacd329b70dc668f69c705eb7118fbd639cd96`, the real proxy CLI, and a
real PostgreSQL 16 database. Raw sanitized captures are in
`transcripts/146/review/`.

| Stage | Caller | Request | N/N | Result |
|---|---|---|---|---|
| cold start | anonymous | `POST /guardrails/register` | 5/5 | 401 `Authentication Error, No api key passed in.` |
| cold start | master key | `POST /guardrails/register` | 1/1 | 200 `{"status":"pending_review",...}` from the real handler |
| cold start | master key | same name again | 4/4 | 400 `Guardrail with name ... already exists` |
| after `POST /anything/register` | anonymous | `POST /guardrails/register` | 5/5 | 401 |
| after `POST /anything/register` | master key | `POST /guardrails/register` | 5/5 | 400 `already exists` from the real handler |
| fresh proxy, MCP router loaded first | anonymous | `POST /guardrails/register` | 5/5 | 401 |
| fresh proxy, MCP router loaded first | master key | `POST /guardrails/register` | 1/1 | 200 `pending_review` from the real handler |
| any stage | anonymous | `POST /anything/register` | 3/3 | 200 dummy MCP client (the MCP route is live) |

Why the route is not shadowed on this commit: both routers are lazy features in
`litellm/proxy/_lazy_features.py`. `guardrails` is the first entry in
`LAZY_FEATURES` and `mcp_discoverable` comes later. Whenever a feature loads,
`_in_registry_order` re-sorts every lazy router into registry order rather than
first-request order, so the static `/guardrails/register` route always sits
ahead of `/{mcp_server_name}/register`. The "MCP router loaded first" rows test
the one order that could have shadowed it, and it still does not.

What the original evidence lacked: `transcripts/146/shadowing_probes.http`
records responses only, with no request bytes, no proxy version or
`litellm.__file__` fingerprint, and no control showing the real handler.
Its 200 dummy body proves that some proxy routed `/guardrails/register` to the
MCP handler, but not that it was the pinned commit. An editable venv that
imports a different LiteLLM checkout is the most likely cause; this was not
confirmed.

What would reopen it: a capture with request bytes and a printed
`litellm.__file__` and commit showing `POST /guardrails/register` answered by
the MCP handler on a current LiteLLM build, including the configuration and the
request order that produce it.

## What breaks

Since commit `0a1dbc0fb4f` (2026-10-27, "drop the experimental label from the
MCP server and client paths"), the MCP OAuth parametric route

```python
@router.post("/{mcp_server_name}/register")
@router.post("/register")
```

(`litellm/proxy/mcp_server/discoverable_endpoints.py:3516-3517`) is always
mounted and matches every two-segment `/X/register` path in registration order,
ahead of the real team guardrail submission endpoint

```python
@router.post("/guardrails/register")   # guardrail_endpoints.py:851-859
async def register_guardrail(..., user_api_key_dict=Depends(user_api_key_auth)):
```

so `POST /guardrails/register` resolves to the MCP handler, which for an unknown
server name returns dummy credentials with HTTP 200 **without authentication**:

```text
anonymous caller:
  POST /guardrails/register {} -> 200 {"client_id":"guardrails",
                                       "client_secret":"dummy", ...}
master key caller:
  POST /guardrails/register {...} -> the same dummy body (the real handler
  never runs; its 401/403 guards are unreachable)
```

The dashboard ships against the real endpoint
(`litellm/ui/litellm-dashboard/src/app/(dashboard)/hooks/guardrails/
useRegisterGuardrail.ts:29`), so the guardrails onboarding flow shows success
while nothing is stored and no admin review can happen. Any `POST /X/register`
path is affected (`POST /anything/register` answers with
`{"client_id":"anything",...}`).

## Wire evidence

`transcripts/146/shadowing_probes.http`:

- anonymous `POST /guardrails/register` -> 200 dummy (the real handler would
  401 through `user_api_key_auth`).
- master-key `POST /guardrails/register` -> the same dummy 200.
- anonymous `POST /anything/register` -> 200 `{"client_id":"anything",...}`.

## Root cause

Route registration order in `litellm/proxy/proxy_server.py`: the MCP
discoverable router's parametric `/register` is registered before the guardrails
router's static `/guardrails/register`, and FastAPI matches the first matching
route. The MCP handler itself is well-formed (unknown server name -> dummy
return, `discoverable_endpoints.py:3534-3559`); the defect is the shadowing.

## Bug or not

- **Expected behavior**: static API routes take precedence over parametric MCP
  OAuth routes; `POST /guardrails/register` runs its auth and team checks
  (`guardrail_endpoints.py:858, 872-888`).
- **Maintainer ruling checked**: none found; searches 2026-11-14.
- **Trigger is supported usage**: both routes are default-mounted features.
- **Boundary crossed**: functional only - the team submission flow never
  executes; no data is exposed (the dummy body is static).
- **Fix a maintainer would ship, in one sentence**: register the MCP parametric
  `/register` route after static API routes (or resolve static-before-parametric)
  so `/guardrails/register` reaches its own handler. Reverse-direction risk:
  deployments that intentionally name an MCP server `guardrails` would start
  hitting the guardrail endpoint instead; the static route must keep winning as
  documented behavior.
- **Label**: `bug`.

## Upstream status

- Date checked: 2026-11-14.
- Classification: **novel**.

## Test

Not added to the Rust harness (route-order invariant of the FastAPI app). The
probe script is inlined above; the invariant is "static API routes win over the
MCP `/{name}/register` parametric route".

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | curl probes | 3/3 shadowed |
| Harness | `cargo test --workspace` | green (markdown-only change) |
| Formatting | `cargo fmt --all -- --check` | green |
| Lint | `cargo clippy --workspace --all-targets --all-features --locked -- -D warnings` | green |
| README counts | `python3 tools/update-readme-counts.py --check` | green |

## Security and scope

- [x] No credentials committed; [x] env var names only; [x] one finding;
- [x] unrelated files excluded.

## Author verdict

Original author verdict, superseded by the independent review below:

- Correctness: PASS
- Usefulness: PASS (label `bug`, functional)
- Upstream status: PASS (novel)
- Overall: ACCEPT

Independent review verdict (2026-11-14): Correctness FAIL (does not reproduce
on the pinned commit), Usefulness and Upstream status not reached, Overall
REJECT. Withdrawn as a false positive.

## Independent review

Run `.github/agents/kairo-reproduction-reviewer.agent.md` against this pull
request.
