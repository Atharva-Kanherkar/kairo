# Finding

## Claim

Bifrost accepts any syntactically recognizable virtual key as sufficient
authentication for `/v1/realtime`, even when that key does not exist. With
`client.enforce_auth_on_inference=true`, a forged `sk-bf-*` value receives a
WebSocket `101` and causes Bifrost to open an upstream Realtime connection with
the operator's configured provider credential.

The ordinary `/v1/models` route rejects the identical forged key with `401`,
and the Realtime route rejects a request with no credential with `401`. The
failure is isolated to the Realtime admission gate.

Demonstrated consequence: two forged-key clients occupied a configured
two-connection Realtime limit. A valid virtual-key client was then upgraded
but immediately received `rate_limit_exceeded`, and no upstream connection was
opened for it.

- Upstream project: [maximhq/bifrost](https://github.com/maximhq/bifrost)
- Cited upstream report: incomplete remediation of merged
  [PR #6759](https://github.com/maximhq/bifrost/pull/6759); no dedicated
  follow-up issue found
- Tested release: Bifrost `v2.1.1`, official `maximhq/bifrost:latest` image at
  digest `sha256:9e65eb4d0b292c25aaf46d194c705344f19c833a29e30155038bbe551eac7245`
- Release source commit: `c193745d2a713e9f58f021d43e138df5eb7e038a`
- Current `dev` checked: `6fbefaf2d71b0386f9ec040ca43001372a22d6fc`
- Client endpoint: `GET /v1/realtime?model=mockoai/mock-model`, WebSocket
- Backend: documented custom OpenAI-compatible provider, deterministic local
  WebSocket capture upstream
- Relevant configuration: `client.enforce_auth_on_inference=true`, one valid
  provider-scoped virtual key, and `websocket.max_connections_per_user=2`

## Gate 1: correctness

### Exact reproduction

```bash
docker pull maximhq/bifrost@sha256:9e65eb4d0b292c25aaf46d194c705344f19c833a29e30155038bbe551eac7245
python3 transcripts/079/reproduce.py
```

The runner requires a Linux host and refuses to proceed otherwise: it starts the
official container with `--network host` and expects it to reach the capture
upstream on `127.0.0.1`, which Docker Desktop for macOS and Windows do not
provide by default. It also refuses to proceed unless the local image resolves
to the pinned digest above. It then writes a synthetic config into a fresh
temporary directory, starts a local capture upstream, and makes five requests in
each admission cell.

The runner writes every upstream handshake twice: once sequentially as
`upstream-handshake-NN.http`, and once under the role-tagged names cited below
and loaded by the conformance fixtures, so a rerun regenerates the cited
evidence rather than a set a reader has to map by hand. The index of each named
capture within the sequence is recorded in `results.json` under
`named_upstream_captures`.

- Expected behavior: with inference authentication enforced, a nonexistent
  virtual key must be rejected before WebSocket upgrade and before Bifrost
  authenticates to an upstream provider.
- Observed behavior: the forged key received `101 Switching Protocols` and
  opened an operator-authenticated upstream connection 5/5.
- Raw client violation: [`forged-realtime.http`](../../transcripts/079/forged-realtime.http)
- Raw upstream consequence: [`upstream-forged-handshake.http`](../../transcripts/079/upstream-forged-handshake.http)
- Full trial counts: [`results.json`](../../transcripts/079/results.json)
- Reproduction rate: forged key 5/5 accepted and 5/5 reached upstream; absent
  credential 5/5 rejected and 0/5 reached upstream; valid key 5/5 accepted and
  5/5 reached upstream.
- Smallest isolated trigger: put any nonexistent value beginning with
  `sk-bf-` in `x-bf-vk` on a supported Realtime WebSocket request.

All credentials are synthetic. The recorded upstream `Authorization` value is
sanitized as `Bearer <BIFROST_PROVIDER_AUTH>`. The script checks for the actual
synthetic value at runtime before writing that sanitized capture.

### Live OpenAI confirmation

On 2026-09-15, the same official Bifrost image was run with a user-supplied,
short-lived OpenAI API key passed only through a silent environment variable.
The key was not written to this repository or included in output. No generation
request was sent.

| Path | HTTP result | First server event |
|---|---:|---|
| no Bifrost credential | 401 | none |
| forged nonexistent Bifrost VK | 101 | `session.created` |
| configured valid Bifrost VK | 101 | `session.created` |

The official OpenAI documentation identifies `session.created` as a Realtime
server event. Receiving it through the forged-key path proves that Bifrost did
not merely contact a permissive mock: OpenAI accepted Bifrost's provider
credential and created a real Realtime session. The sanitized result is
[`live-results.json`](../../transcripts/079/live-results.json), and
[`live_verify.py`](../../transcripts/079/live_verify.py) is the key-safe runner.
The temporary runtime directory was deleted immediately after the test because
its SQLite state could contain resolved configuration values.

### Control matrix

| Cell | Credential | Client result | Upstream handshakes | Verdict |
|---|---|---:|---:|---|
| Realtime negative control | absent | 401, 5/5 | 0/5 | pass |
| Realtime violation | forged nonexistent VK | 101, 5/5 | 5/5 | fail |
| Realtime positive control | configured valid VK | 101, 5/5 | 5/5 | pass |
| ordinary HTTP isolation control | same forged VK | 401, 1/1 | 0/1 | pass |

The corresponding frozen exchanges are
[`absent-realtime.http`](../../transcripts/079/absent-realtime.http),
[`valid-realtime.http`](../../transcripts/079/valid-realtime.http), and
[`control-http-forged-key.http`](../../transcripts/079/control-http-forged-key.http).

The ordinary HTTP control matters because it uses the same Bifrost process,
configuration, forged bytes, and provider. It proves the key is not present in
the configured governance store and that authentication enforcement is active.
Only the Realtime admission path turns presence of those bytes into admission.

### Consumer-boundary impact

After a valid-key baseline opened an upstream session, the runner held two
forged-key Realtime sessions open. With the supported connection-limit setting
at two, another valid-key client received:

```json
{
  "type": "error",
  "error": {
    "type": "rate_limit_exceeded",
    "code": "rate_limit_exceeded",
    "message": "websocket connection limit reached"
  }
}
```

The full client exchange is
[`impact-valid-after-forged-saturation.http`](../../transcripts/079/impact-valid-after-forged-saturation.http),
and the decoded first frame is
[`impact-valid-first-frame.json`](../../transcripts/079/impact-valid-first-frame.json).
The exact WebSocket frame bytes are retained as hex in
[`impact-valid-first-frame.hex`](../../transcripts/079/impact-valid-first-frame.hex).
The two forged sessions' sanitized upstream requests are
[`upstream-impact-forged-1.http`](../../transcripts/079/upstream-impact-forged-1.http)
and
[`upstream-impact-forged-2.http`](../../transcripts/079/upstream-impact-forged-2.http).
The runner asserts that the blocked valid client opened no upstream session.

The limit was reduced from its default of 100 to make the denial deterministic
with two local sockets. This changes only the number of forged connections
needed, not whether each forged connection is admitted or consumes a slot.

### Root cause

The release admission gate at
[`realtimeauthgate.go:38-58`](https://github.com/maximhq/bifrost/blob/c193745d2a713e9f58f021d43e138df5eb7e038a/transports/bifrost-http/handlers/realtimeauthgate.go#L38-L58)
returns success when `governance.PresentedAnyCredential` is true. It validates
an `ek_` ephemeral token by lookup, but does not resolve or validate a presented
virtual key.

The predicate's own contract at
[`plugins/governance/utils.go:211-229`](https://github.com/maximhq/bifrost/blob/c193745d2a713e9f58f021d43e138df5eb7e038a/plugins/governance/utils.go#L211-L229)
says it answers only whether a credential was presented, never whether it
grants the requested access. A forged `sk-bf-*` value therefore satisfies the
gate.

The source deliberately makes this a presence-only gate, but it does not rule
that nonexistent strings are valid authentication. The same comments call the
accepted virtual-key case a "grant-bearing" credential and say the gate exists
to stop an anonymous client from opening an upstream session on the operator's
key. A value absent from the governance store grants nothing, leaves the caller
anonymous, and produces exactly the upstream session those comments intend to
prevent. The separate lookup and rejection of forged `ek_` values shows the
same gate already treats syntactic token presence as insufficient for the
other supported Realtime credential type.

After that gate, the handler runs pre-request hooks but receives no admission
verdict, upgrades the client, selects the operator's configured key, constructs
provider authentication headers, and calls the upstream pool. The current flow
is visible in
[`wsrealtime.go:126-218`](https://github.com/maximhq/bifrost/blob/6fbefaf2d71b0386f9ec040ca43001372a22d6fc/transports/bifrost-http/handlers/wsrealtime.go#L126-L218)
and
[`wsrealtime.go:426-467`](https://github.com/maximhq/bifrost/blob/6fbefaf2d71b0386f9ec040ca43001372a22d6fc/transports/bifrost-http/handlers/wsrealtime.go#L426-L467).

The release and current `dev` copies of `realtimeauthgate.go` are byte-identical,
sha256:

```text
7c2726032eeb131ed7df6518dc0813608f96e92a857e4a25ab7d4cfef8e13b6f
```

That digest is not asserted from prose.
[`verify_gate_identical.py`](../../transcripts/079/verify_gate_identical.py)
refetches the file at both commits, recomputes the digest, and fails if the two
commits differ or if the digest has moved. Its recorded result is
[`gate-identity.json`](../../transcripts/079/gate-identity.json): 3157 bytes,
identical at both commits, checked 2026-09-16.

One-sentence fix: resolve and validate the presented virtual key or authenticated
identity, including provider/model access, before client upgrade and upstream
dial, while preserving the separately validated ephemeral-secret flow and
avoiding double-counting per-turn limits.

### False-positive checks

- [x] Tested the exact public behavior, not a neighboring symptom.
- [x] Pinned the official image by immutable digest and recorded its runtime version.
- [x] Matched the release to its source tag and checked the current `dev` gate.
- [x] Used a documented route, configuration field, and custom-provider form.
- [x] Ruled out disabled authentication with absent and ordinary-route controls.
- [x] Ruled out an accidentally valid key with the ordinary-route control.
- [x] Ruled out mock-created admission: the mock sees traffic only after Bifrost admits it.
- [x] Ruled out model nondeterminism: no model generation occurs.
- [x] Sanitized all recorded evidence and used no real secrets.

## Gate 2: usefulness

### Who gets bitten

- Affected operator: anyone exposing Bifrost Realtime with inference auth
  enforced and operator-managed provider credentials.
- Real workflow: browsers, voice agents, and other Realtime clients connect to
  the documented `/v1/realtime` route using virtual keys or ephemeral secrets.
- Attacker prerequisite: network access to that route and knowledge of the
  public `sk-bf-` virtual-key shape. No valid Bifrost or provider credential is
  required.
- Frequency: deterministic for every tested forged key, 5/5. Repeating enough
  held connections reaches the configured process limit.

### Observable consequence

- Wire-level defect: an unauthenticated caller receives a successful WebSocket
  upgrade and causes an upstream handshake authenticated with Bifrost's provider
  credential.
- Measured end-user failure: with two forged sessions held, a separately valid
  virtual-key caller receives `rate_limit_exceeded` and opens no upstream
  session.
- Measured resource use: each forged client occupied one Bifrost session and one
  live upstream WebSocket in the capture server.
- Inferred, bounded impact: on a default limit of 100, an attacker needs more
  concurrent sockets but can apply the same deterministic admission bypass.
  Provider-side connection quotas or connection charges may also be consumed;
  those provider-specific effects were not measured.

This report does not claim that a forged client can complete a model turn.
Turn-level governance may still reject later events. The demonstrated boundary
crossing happens earlier: connection admission, Bifrost session allocation,
operator-authenticated upstream dial, and denial of a valid user's connection.

## Gate 3: current upstream status

Checked 2026-09-15 against current `dev` commit
`6fbefaf2d71b0386f9ec040ca43001372a22d6fc`.

- Merged [PR #6759](https://github.com/maximhq/bifrost/pull/6759)
  introduced this admission gate on 2026-09-02 as a security fix. Its manual
  test says a valid virtual key should be accepted, and its security section
  says anonymous clients must not open upstream sessions on the operator's key.
  The forged-key case is therefore an incomplete-remediation edge case in that
  fix, not a wholly novel finding.
- Current docs say `enforce_auth_on_inference` requires authentication on
  `/v1/*` and that Realtime connections are refused with 401 at connect time
  unless they present a virtual key, API key, user token, or valid ephemeral
  secret: [client configuration](https://github.com/maximhq/bifrost/blob/dev/docs/deployment-guides/config-json/client.mdx).
- The virtual-key docs show `/v1/models` as a supported virtual-key route:
  [virtual keys](https://github.com/maximhq/bifrost/blob/dev/docs/features/governance/virtual-keys.mdx).
- Issue searches:
  [Realtime virtual-key authentication](https://github.com/maximhq/bifrost/issues?q=is%3Aissue+realtime+%22virtual+key%22+authentication),
  [exact refusal text](https://github.com/maximhq/bifrost/issues?q=is%3Aissue+%22authentication+is+required+for+realtime+connections%22),
  and
  [invalid-key Realtime](https://github.com/maximhq/bifrost/issues?q=is%3Aissue+realtime+%22invalid%22+%22virtual+key%22)
  found no matching report.
- Pull-request searches for Realtime virtual-key authentication and
  `PresentedAnyCredential` found PR #6759, but no follow-up that validates a
  normal virtual key at admission:
  [Realtime virtual-key authentication](https://github.com/maximhq/bifrost/pulls?q=is%3Apr+realtime+%22virtual+key%22+authentication)
  and
  [`PresentedAnyCredential`](https://github.com/maximhq/bifrost/pulls?q=is%3Apr+PresentedAnyCredential).
- Related but distinct: [#2994](https://github.com/maximhq/bifrost/issues/2994)
  concerns discarded upstream error details after a WebSocket dial failure. It
  does not concern admission of an invalid Bifrost credential.

No maintainer comment, issue, pull request, or documented exception was found
that classifies nonexistent virtual keys as valid Realtime admission. PR #6759
instead specifies a valid virtual key for the accepted control and describes
preventing anonymous operator-key sessions as its objective. The tested release
and current `dev` use the same admission-gate bytes.

Classification: `novel`. No issue, pull request, discussion, or documented
exception covers admission of a nonexistent virtual key on `/v1/realtime`.
Merged [PR #6759](https://github.com/maximhq/bifrost/pull/6759) is the closest
prior art and is cited here as the incompletely remediated fix, but it is merged
and closed, so this is not `duplicate-open`. The behavior reproduces on the
tested release and the admission gate is byte-identical on current `dev`, so it
is not `fixed`. The documentation promises connect-time refusal, so it is not
`documented-behavior`, and no upstream discussion of the forged-key case was
found, so it is not `discussed-no-ticket`.

## Bug-or-not verdict

- Expected behavior source: Bifrost's authentication setting and its Realtime
  documentation promise connect-time refusal; the ordinary route enforces it.
- Trigger validity: supported route, supported key header, supported custom
  provider, and supported connection-limit configuration.
- Real boundary crossed: an unauthenticated external client allocates gateway
  and upstream sessions under an operator credential and can deny a valid
  caller service.
- Maintainer actionability: replace presence-only admission with validation
  before upgrade and dial.

Label: `bug` (authentication bypass with demonstrated availability impact).

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| Nonexistent VK receives Realtime 101 | High | raw bytes, 5/5 |
| Nonexistent VK opens provider-authenticated upstream WS | High | sanitized capture 5/5 and live OpenAI `session.created` 1/1 |
| Authentication is enabled and key is invalid | High | absent and ordinary-route controls |
| Forged sessions deny a valid-key client | High | held-socket impact leg and decoded error |
| Root cause is presence-only admission | High | exact source contract and matching runtime behavior |
| Current `dev` remains affected | High | gate is byte-identical; runtime rerun was on release, not `dev` |
| Provider billing impact | Untested | live session creation only; no generation request sent |

## Test

`crates/harness/tests/conformance.rs` freezes the invariant with the shared
`invalid_credential_rejected_before_upstream` checker:

- `bifrost_realtime_forged_virtual_key_reaches_upstream`, the violation.
- `bifrost_http_forged_virtual_key_is_rejected_before_upstream`, same-key
  passing control.
- `bifrost_realtime_auth_checker_has_nonvacuous_controls`, absent and valid
  polarity controls, plus a vacuity guard. The forged case already fails on its
  `101` status, so the guard pairs a rejected `401` with a real upstream
  handshake and requires a violation. A checker that stopped consulting whether
  the request reached upstream fails there instead of passing as a false green.

Invariant: *an invalid credential is rejected at the gateway boundary before
the gateway authenticates any connection to an upstream provider.*
