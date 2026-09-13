# 077 Bifrost custom provider response secret leak: test contract

## Functional behavior

- Test official Bifrost `dev` at
  `44a562431ee0463cb1afe0e5833cde07d7921701`, core 1.8.6.
- Exercise the real `POST /v1/responses` public entry point with
  `client.enforce_auth_on_inference=true`, `governance.auth_config.is_enabled=true`,
  and a provider-scoped virtual key.
- Configure a custom OpenAI-compatible provider with static
  `network_config.extra_headers.X-Provider-Secret` and a synthetic secret value.
- Use a deterministic capture upstream that returns a valid OpenAI Responses body
  and reflects that configured header.
- Run five trials and require the secret in both the client HTTP header and
  `extra_fields.provider_response_headers` on every trial.
- In the same upstream response, return an `Authorization` control that must be
  stripped and a benign `X-Safe-Trace` control that must be preserved.
- Repeat the same request without the virtual key. It must return 401 and must not
  expose the provider secret.
- Present the same virtual key to `GET /api/providers`. Management auth is enabled
  and upstream builds `APIMiddleware` with `allowVirtualKeyAuth=false`, so it must
  return 401 or 403 and must not expose the provider secret. This is the privilege
  gap the leak crosses: the caller cannot read provider config through the
  supported path, yet the inference response hands it the configured header value.
- Store literal client and upstream request and response bytes. Replace the
  virtual key and provider Authorization values before writing artifacts.

## Invariant coverage

- Reuse `response_omits_secret` because the invariant is that a complete
  client-visible response must omit an administrator's provider credential.
- The violating fixture must fail the invariant for
  `CANARY_ADMIN_ONLY_PROVIDER_SECRET_077`.
- The expected fixture and unauthenticated control must pass the same invariant.
- The observed fixture must omit `CANARY_AUTHORIZATION_CONTROL_077`, proving the
  existing exact-name filter works.
- The observed fixture must contain `CANARY_SAFE_TRACE_077`, proving benign
  provider metadata still passes.
- The management-boundary fixture must target `GET /api/providers`, carry the
  sanitized virtual key, return 401, and pass the same invariant.
- The summary must record `management_auth_enabled: true`, so a future run with
  the management API left open cannot satisfy the boundary claim silently.
- Assert the route and statuses before applying the canary checker so a swapped or
  error fixture cannot satisfy the test accidentally.

## Reproduction safeguards

- Verify the source revision, `core/version`, relevant-source cleanliness, and
  runtime binary version before sending trials. The pinned set covers all three
  extractors and their shared denylist (`core/providers/utils/utils.go`), the
  sensitive-name classifier (`core/schemas/trace.go`), its call sites
  (`core/schemas/headers.go`), the `ExtraHeaders` definition
  (`core/schemas/provider.go`), and the handler that copies the map onto the
  client response (`transports/bifrost-http/handlers/inference.go`).
- The runner asserts the leak is present, so it exits non-zero against a patched
  Bifrost. That inverted failure is the acceptance signal for a fix, not a fault.
- `expected.http` is synthesized by the runner from the observed exchange with the
  secret removed. It is a passing counterpart fixture, not a recording.
- Give Bifrost an allowlisted child environment without provider credentials.
- Use only synthetic canaries and a loopback upstream.
- Validate every trial before writing any fixture.
- Default reviewer runs write to a fresh ignored temporary directory.
- Stop Bifrost and the capture server in all exit paths.

## Commands

```text
BIFROST_SOURCE=/path/to/bifrost \
BIFROST_BIN=/path/to/bifrost-http \
python3 transcripts/077/reproduce.py

cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
python3 tools/update-readme-counts.py --check
```

Independent read-only review remains required before merge approval.
