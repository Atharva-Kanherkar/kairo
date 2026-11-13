# 143, LiteLLM Admin-UI SSO links any co-tenant IdP login with an unverified email to an existing account, including the proxy admin, and the binding persists

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). The same
  mechanism in the JWT bearer flow is published as
  [CVE-2026-93355 / GHSA-g83g-3c4g-cfm5](https://github.com/advisories/GHSA-g83g-3c4g-cfm5)
  (High, published 2026-09-28). The Admin-UI SSO flow recorded here is
  acknowledged verbatim in the "Before" section of the open, unmerged fix pull
  request [#37085](https://github.com/BerriAI/litellm/pull/37085) (created
  2026-08-16, updated 2026-11-05). The defect is still present on current main
  `6abacd329b7` (2026-11-13).
- **Recorded as**: `duplicate-open` with an independent wire reproduction on a
  second entry point, a control, a fixed-build control, and a persistence
  demonstration. Not filed upstream, because the maintainer fix PR already
  exists and is unmerged.
- **Tool under test**: LiteLLM at main `6abacd329b70dc668f69c705eb7118fbd639cd96`
  (upstream default branch, 2026-11-13), run through the real proxy CLI
  (`litellm --config ... --port 4100`) with a real PostgreSQL 16 database
  (Docker, scratch). Python 3.12.13, `prisma` 0.11.0,
  `litellm-proxy-extras` 0.4.115. The identity provider is a local OIDC stub on
  `127.0.0.1:9600` wired in through `GENERIC_CLIENT_ID`,
  `GENERIC_AUTHORIZATION_ENDPOINT`, `GENERIC_TOKEN_ENDPOINT` and
  `GENERIC_USERINFO_ENDPOINT`, the documented generic-SSO configuration.
- **Reproduced**: 2026-11-13/14, local. 5 of 5 over HTTP for the attack cell,
  plus a no-match control cell and a persisted-binding cell.
- **Label**: `bug`. Severity High (P2 in LiteLLM's own policy, which pays no
  bounty for P2; recorded because the same root cause is published and the fix
  is unmerged).

## What breaks

LiteLLM's SSO sign-in resolves the LiteLLM account behind an IdP login in three
steps: by `user_id`, by `sso_user_id`, and then by a case-insensitive email
match (`_get_fuzzy_user_object`). The email fallback runs without requiring the
identity provider to assert `email_verified: true`. No other step on the path
checks it: `email_verified` appears in exactly two files in the proxy (the MCP
OAuth identity binding, where the default is `require_email_verified: bool =
True`, and its enforcement), and in neither `ui_sso.py` nor the user lookup
helpers.

A login at the operator's configured IdP that carries an unverified email equal
to an existing user's email therefore binds to that account. The proxy then
mints the Admin-UI session with the victim's `user_id` and the victim's stored
`user_role`, and stamps the attacker's IdP subject over the victim's
`sso_user_id`, which makes the binding persist across later logins.

```text
Attacker (account in the operator's IdP, unverified email = admin's email)
  -> GET /sso/key/generate                      (no auth needed)
  -> IdP authorize + token + userinfo           (attacker's own subject)
  -> GET /sso/callback?code=...&state=...       (unauthenticated)
     account matched by email, no email_verified gate
     session minted: user_id=<victim>, user_role=proxy_admin
     victim.sso_user_id <- attacker's subject (persistent rebind)
  <- 303 Set-Cookie: token=<JWT: user_role=proxy_admin>
  -> POST /key/generate with the session key    200 (admin action)
  -> GET  /user/list                            200 (admin-only route)
```

The proxy admin's dashboard, key generation, budget controls and spend logs are
all reachable as the victim. The victim's own subject can no longer match its
account, because the row's `sso_user_id` now holds the attacker's subject.

Preconditions: SSO configured (a default feature, not enterprise-gated) and an
IdP that lets a principal hold an unverified email colliding with an existing
user's email (common on self-hosted OIDC with self-registration and verify-email
disabled). Google logins are additionally blocked: fastapi-sso's Google handler
raises `SSOLoginError` when `email_verified` is falsy. Microsoft OAuth is
exposed (the Graph `mail` field carries no verification flag) and generic OIDC
is fully exposed.

## Cells

Every attack cell is 5 of 5 over HTTP against the real proxy on the pinned
commit. The state cookie is carried per hop exactly as a browser does; the
proxy's OAuth state verification works and is not part of the defect.

| Cell | IdP subject | Email claim | email_verified | Account matched | Session role | Admin routes |
|---|---|---|---|---|---|---|
| attack (x5) | `attacker-unverified-sub` | victim's email | `false` | victim `u-admin2` by email fallback | `proxy_admin` | 200 |
| persisted (x2) | any subject | any email | `false` | victim by the stamped `sso_user_id` first | `proxy_admin` | 200 |
| control (no match) | `control-fresh-sub` | nobody's email | `false` | none, new account created | `internal_user_viewer` | 403/401 |
| control (fixed build) | attacker subject | victim's email | gated out | none | `internal_user_viewer` | 403/401 |

The control changes only the email and subject the IdP presents; the same login
flow then creates a fresh `internal_user_viewer` that is denied
`/user/list` (403) and `/key/generate` (401). The fixed-build control is the
same lookup function with an `email_verified` gate added, which returns None
and prevents the bind (unit-level, `repro/`).

## Wire evidence

All of `transcripts/143/`, captured with `curl -i` hop by hop, sanitized: the
scratch master key, database password, every minted virtual key and every minted
session JWT are replaced with marked placeholders; the JWT claims quoted below
are decoded values of the recorded cookie.

- `seed_user.http`: victim creation. `POST /user/new` with the master key,
  `{"user_id":"u-admin2","user_email":"admin@corpdemohunt.com","user_role":"proxy_admin"}`,
  200.
- `attack_hop1_sso_start.http`: `GET /sso/key/generate`, 303, `Location` to the
  IdP authorize endpoint with `state=...`, `Set-Cookie: sso_state=...`.
- `attack_hop2_fake_authorize.http`: the IdP redirect back to
  `/sso/callback?code=repro-code&state=...`.
- `attack_hop3_callback.http` and `attack_run1..4_callback.http`: the callback
  responses, 303 each, each setting the session cookie. Decoded claims of the
  run-1 cookie:

```json
{"user_id":"u-admin2","key":"sk-SCRATCH-REDACTED[session-2a]",
 "user_email":"admin@corpdemohunt.com","user_role":"proxy_admin",
 "login_method":"sso","premium_user":false,"auth_header_name":"Authorization",
 "disabled_non_admin_personal_key_creation":false,"server_root_path":"",
 "password_reset_required":false}
```

- `attack_hop5_mint_key.http`: with the session key as bearer, `POST
  /key/generate` returns 200 and mints a new key
  (`key_alias: stolen-admin-mints-a-key`), an admin action.
- `attack_hop6_admin_read.http`: `GET /user/list`, an admin-only route, 200.
- `attack_hop7_victim_row.http`: `GET /user/info?user_id=u-admin2`, 200, shows
  `sso_user_id` is now the attacker's subject and the role is unchanged:
  `{"sso_user_id":"attacker-unverified-sub","user_role":"proxy_admin",...}`.
- `control_hop1..3.http`: the no-match cell. The debug log records the insert
  (`user_role: internal_user_viewer`, new `user_id: control-fresh-sub`), and the
  control key gets 403 on `/user/list` and 401 on `/key/generate`.
- `sso_email_link_repro.txt`: 5 of 5 of the unit-level run of the real handler
  chain (attack matched and stamped, no-match control returns None, gated
  control returns None).

The IdP stub and the two repro scripts are committed under `issues/143-*/repro/`
with the exact commands in the reproduction section.

## Root cause

LiteLLM main `6abacd329b7`:

- `litellm/proxy/auth/auth_checks.py:2958` (`_get_fuzzy_user_object`): after the
  `sso_user_id` lookup misses, the email fallback at line 2984 runs
  `find_first(where={"user_email": {"equals": email, "mode": "insensitive"}})`
  with no `email_verified` requirement, and the background task at lines
  2990-2993 stamps `data={"sso_user_id": sso_user_id}` onto the matched row.
- `litellm/proxy/management_endpoints/ui_sso.py:2006`
  (`get_user_info_from_db`) feeds the IdP `email` claim into that lookup and
  `upsert_sso_user` (line 3651) then rewrites the matched row's `user_email`.
- `litellm/proxy/management_endpoints/ui_sso.py:3932`
  (`get_redirect_response_from_openid`) and `:2199`
  (`apply_user_info_values_to_sso_user_defined_values`) apply the matched row's
  `user_id` and `user_role` to the session, and `:4037` mints the UI session key
  with them.
- The codebase's own invariant for binding an IdP email principal lives in
  `litellm/types/mcp_server/mcp_server_manager.py:83`
  (`require_email_verified: bool = True`) with the enforcement in
  `litellm/proxy/mcp_server/oauth_identity_binding.py:172-180`. The SSO
  account-linking path skips that invariant.

## Bug or not

- **Expected behavior**: matching an SSO login by email is intended (there are
  tests for the email-fallback match, e.g.
  `tests/unit/proxy/management_endpoints/test_ui_sso.py:2231`); matching on an
  **unverified** email is not supported anywhere: the only in-repo equivalent
  (MCP identity binding) requires verification by default, and the maintainers'
  own fix PR describes the current behavior as the bug ("Unverified emails
  could trigger unsafe SSO/JWT account linking... Proxy admins could have their
  identity and privileges taken over", PR #37085 body).
- **Maintainer ruling checked**: PR #37085 is open, not merged (verified
  2026-11-14, `git merge-base --is-ancestor` on its head commit is false; zero
  commits containing `email_verified` ever touched `ui_sso.py` or the user
  lookup helpers). No merged commit accepts the unverified-email binding.
- **Trigger is supported usage**: generic OIDC SSO is a documented, free-tier
  configuration; the login flow is the product's own sign-in. The unverified
  email lives in the operator's IdP, outside LiteLLM's configuration surface;
  LiteLLM's own module shows the binding decision belongs to LiteLLM.
- **Boundary crossed**: the attacker authenticates with their own IdP identity
  and lands in the victim's LiteLLM account. The role that must not see the
  data (`internal_user_viewer`, or an unrelated user) ends up operating the
  `proxy_admin` account's dashboard, keys, budgets and logs.
- **Fix a maintainer would ship**: require `email_verified === true` before the
  email fallback matches, and refuse to rebind an account that already carries a
  different `sso_user_id` or an admin role; ship it as a policy with a safe
  default (PR #37085 implements exactly this).
- **Label**: `bug`.

## Upstream status

- Date checked: 2026-11-13/14.
- Checked: upstream default branch `6abacd329b7` (2026-11-13), published
  advisories via the GitHub advisory API (19 for `BerriAI/litellm`), open issues
  and PRs via `gh search`, and PR #37085's body and merge state.
- Search terms: "email_verified", "unverified email", "sso account takeover",
  "sso impersonation", "email collision", "sso email", "fuzzy user", "sso email
  matching", "email_verified sso" (issues and PRs).
- Matching links: [CVE-2026-93355 / GHSA-g83g-3c4g-cfm5](https://github.com/advisories/GHSA-g83g-3c4g-cfm5)
  (the JWT bearer flow, same root cause), open fix PR
  [#37085](https://github.com/BerriAI/litellm/pull/37085).

Classification: **duplicate, open and still reproducible** (sibling entry
point). What is new here: an independently reproduced wire-level record on a
second, default-config entry point (the Admin-UI SSO login that every operator
uses), the Microsoft-OAuth exposure (Google is handler-protected, Microsoft is
not), the persistent rebinding behavior over the wire, and the codebase-invariant
argument (the MCP binding's `require_email_verified: bool = True`).

## Test

Not added to the Rust harness: the invariant lives behind the proxy's SSO flow,
a PostgreSQL database and an IdP round-trip, which the byte-replay harness does
not drive. The invariant-level record is the scripted reproduction committed at
`issues/143-*/repro/` (unit-level handler run plus the wire script), with
commands a reviewer can run from a clean checkout of the litellm worktree. The
invariant, not the bug: "an SSO sign-in whose email claim is not
IdP-verified must never match an existing account by email; an account bound to
one IdP subject must never be rebound to another."

## Reproduction (commands)

```text
# 1. database (scratch): docker run -d --name litellm-hunt-db -e POSTGRES_PASSWORD=<scratch> \
#      -e POSTGRES_DB=litellm -p 127.0.0.1:55432:5432 postgres:16-alpine
# 2. client generation, in the pinned litellm worktree, python 3.12 venv:
#      uv pip install -p .venv-hunt/bin/python "prisma==0.11.0" "pydantic==2.13.4" \
#        "psycopg[binary]" litellm-proxy-extras(no-deps, editable ./litellm/proxy-extras)
#      PATH=.venv-hunt/bin:$PATH prisma generate --schema litellm/schema.prisma
# 3. IdP stub:   .venv-hunt/bin/python issues/143-*/repro/fake_idp.py
# 4. proxy:      env LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY \
#      DATABASE_URL=$LITELLM_DATABASE_URL STORE_MODEL_IN_DB=True \
#      GENERIC_CLIENT_ID=... GENERIC_CLIENT_SECRET=... \
#      GENERIC_AUTHORIZATION_ENDPOINT=http://127.0.0.1:9600/authorize \
#      GENERIC_TOKEN_ENDPOINT=http://127.0.0.1:9600/token \
#      GENERIC_USERINFO_ENDPOINT=http://127.0.0.1:9600/userinfo \
#      .venv-hunt/bin/litellm --config repro/config.yaml --host 127.0.0.1 --port 4100
# 5. victim:     curl -X POST /user/new -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
#      -d '{"user_id":"u-admin2","user_email":"admin@corpdemohunt.com","user_role":"proxy_admin"}'
# 6. attack:     follow /sso/key/generate -> IdP authorize -> /sso/callback with the
#      sso_state cookie; decode the token cookie's middle segment.
# Secret env var names only: LITELLM_MASTER_KEY, LITELLM_DATABASE_URL,
# GENERIC_CLIENT_ID, GENERIC_CLIENT_SECRET.
```

Expected: the callback mints a session whose claims carry the victim's
`user_id` and `proxy_admin` despite `email_verified: false`. Observed: 5 of 5.
Smallest isolated trigger: `_get_fuzzy_user_object`'s email fallback running
without a verification gate.
