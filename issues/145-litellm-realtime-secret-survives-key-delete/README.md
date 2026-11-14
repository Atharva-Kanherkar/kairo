# 145, LiteLLM realtime client secrets keep executing model calls after their authorizing key is deleted, because the only expiry embedded in the token is whatever the upstream echoes back

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). Open fix PR #51381 covers it; the original draft said no upstream
  issue or PR covers the revocation gap.
- **Recorded as**: `duplicate-open` (corrected 2026-11-14 from `novel`, see
  Upstream status), wire evidence 5/5, control, and the code path pinned.
- **Tool under test**: LiteLLM at main `6abacd329b70dc668f69c705eb7118fbd639cd96`
  (upstream default branch, 2026-11-13), real proxy CLI + real PostgreSQL 16
  (Docker) + a local deterministic OpenAI-compatible upstream (also standing in
  for the realtime session creation). Python 3.12.13.
- **Reproduced**: 2026-11-14, local. 5 of 5 over HTTP.
- **Label**: `bug` (authenticated lifecycle/revocation gap; P2 in LiteLLM's
  policy). Severity argument for lower: with a real provider upstream the
  upstream's own ephemeral-key TTL usually bounds the orphaned token's life to
  minutes; the proxy-side hole is that nothing in LiteLLM enforces or bounds it.

## What breaks

`POST /realtime/client_secrets` asks the deployment's upstream for an ephemeral
session, then wraps the upstream's ephemeral key plus the caller's auth snapshot
into a payload and returns it encrypted with the salt key as the "client
secret". `POST /realtime/calls` treats that encrypted blob as a bearer token:
it decrypts it (decryption is the only authenticity check), checks `expires_at`
**only if the upstream put one in the session response** (an int check, so a
missing field means the token never expires), and reconstructs the caller's
`UserAPIKeyAuth` from the token - it never re-reads the key row.

Consequences with an upstream that returns no `expires_at`:

```text
operator: POST /key/generate (key K)            200
attacker with K: POST /realtime/client_secrets  200 (secret S, expires_at: null)
operator: POST /key/delete {K}                  200   (K is gone; /key/info 401s)
attacker: POST /realtime/calls with S           200   <- a full model call, billed
                                                  to the deleted key's user
```

The revocation surface LiteLLM exposes to operators (delete, block, expire a
key) does not reach outstanding realtime secrets. The same applies to key
expiry: the token outlives `expires` on the key because the key row is never
consulted again.

## Cells

Every cell is 5 of 5 over HTTP against the real proxy on the pinned commit.

| Cell | Steps | Result |
|---|---|---|
| attack (x5) | mint key K, `POST /realtime/client_secrets` with K, `POST /key/delete {K}`, `POST /realtime/calls` with S | 5/5: 200 and a real completion (spend billed to the deleted key's user) |
| control (live key) | the same flow without the delete | 200 (the endpoint works as intended for a live key) |
| control (key lifecycle enforced elsewhere) | same key deleted, then `GET /key/info` with K | 401 - the proxy enforces deletion on the normal key path, only the realtime token bypasses it |

## Wire evidence

All of `transcripts/145/`, sanitized (scratch keys replaced with marked
placeholders).

- `attack_run1..5.http`: the four steps per run; the final `POST
  /realtime/calls` with the orphaned token returns 200 and the mock completion.
- `control_key_info_after_delete.http`: the deleted key 401s on `/key/info`,
  proving deletion is enforced on the ordinary path.

## Root cause

LiteLLM main `6abacd329b7`:

- `litellm/proxy/realtime_endpoints/endpoints.py:466-489`
  (`create_realtime_client_secret`): encodes `expires_at` from the upstream
  session response (`expires_at if isinstance(expires_at, int) else None`) and
  the caller auth snapshot into the encrypted token.
- `litellm/proxy/realtime_endpoints/endpoints.py:536-596`
  (`proxy_realtime_calls`): decrypts the token, checks expiry only when
  `expires_at` is an int (line 563-564), and rebuilds the caller identity from
  the token (`_caller_auth_from_token`, line 294-300) without consulting the
  key row, its `blocked`/`expires` state, or any server-side revocation list.

## Bug or not

- **Expected behavior**: key deletion/block/expiry is the operator's revocation
  control (enforced on every other route); the docs describe client secrets as
  short-lived session credentials.
- **Maintainer ruling checked**: none found; searches on 2026-11-14 for
  "realtime client secret", "realtime/calls token", "realtime ephemeral".
- **Trigger is supported usage**: the realtime GA routes are a default feature;
  the caller supplies no non-default configuration (an upstream that does not
  echo `expires_after` is enough).
- **Boundary crossed**: a credential the operator revoked continues to spend
  the tenant's budget under the deleted key's identity.
- **Fix a maintainer would ship, in one sentence**: enforce a proxy-side maximum
  TTL on realtime tokens (independent of the upstream echo) and check the issuing
  key's current row (blocked/deleted/expired) when the token is spent.
  Reverse-direction risk: WebRTC renegotiations mid-session could break if the
  check is too strict; scope it to new call setups.
- **Label**: `bug`.

## Upstream status

- Date checked: 2026-11-14.
- Checked: default branch `6abacd329b7`; searches "realtime client secret",
  "realtime/calls token", "realtime ephemeral" (issues and PRs); adjacent closed
  issues are model-resolution bugs, not the revocation gap.
- Classification: **duplicate-open**, corrected 2026-11-14 from `novel`. Open
  upstream PR [BerriAI/litellm#51381](https://github.com/BerriAI/litellm/pull/51381)
  (created 2026-11-12) binds v2 realtime tokens to the hash of the minting key
  and re-resolves that key on every `/v1/realtime/calls` request, so a deleted
  or blocked key invalidates its outstanding tokens. Do not file a new report.

## Test

Not added to the Rust harness (the invariant needs the proxy, a database and an
upstream session response). The scripted reproduction is committed at
`issues/145-*/repro/realtime_orphan_repro.py`. The invariant, not the bug:
"spending a realtime client secret must re-validate the issuing key's current
lifecycle state."

## Reproduction (commands)

```text
# proxy on 127.0.0.1:4100 with master key + database (names only):
#   LITELLM_MASTER_KEY, LITELLM_DATABASE_URL
#   .venv-hunt/bin/python issues/145-*/repro/realtime_orphan_repro.py
```

Expected: the orphaned secret is refused or expires. Observed: 5 of 5 calls
execute. Smallest isolated trigger: the int-only `expires_at` check at
`endpoints.py:563-564` combined with minting under an upstream that echoes no
expiry.
