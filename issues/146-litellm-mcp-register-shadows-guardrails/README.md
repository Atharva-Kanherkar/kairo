# 146, LiteLLM's MCP OAuth `POST /{mcp_server_name}/register` shadows `POST /guardrails/register`, so the team guardrail submission endpoint is unreachable (and returns 200 dummy credentials to anonymous callers)

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). No upstream
  issue or PR covers it (searched 2026-11-14: "guardrails/register",
  "guardrails register route").
- **Recorded as**: `novel` functional regression, wire evidence, deterministic.
- **Tool under test**: LiteLLM at main `6abacd329b70dc668f69c705eb7118fbd639cd96`
  (upstream default branch, 2026-11-13), real proxy CLI. Python 3.12.13.
- **Reproduced**: 2026-11-14, local. 3 of 3 probes.
- **Label**: `bug` (functional: a shipped feature is dead and fails silently;
  no data exposure).

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

- Correctness: PASS
- Usefulness: PASS (label `bug`, functional)
- Upstream status: PASS (novel)
- Overall: ACCEPT

## Independent review

Run `.github/agents/kairo-reproduction-reviewer.agent.md` against this pull
request.
