# 077 Bifrost custom provider response secret leak: test contract

## Functional behavior

- Test official Bifrost `dev` at
  `44a562431ee0463cb1afe0e5833cde07d7921701`, core 1.8.6.
- Exercise the real `POST /v1/responses` public entry point with
  `client.enforce_auth_on_inference=true` and a provider-scoped virtual key.
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
- Assert the route and statuses before applying the canary checker so a swapped or
  error fixture cannot satisfy the test accidentally.

## Reproduction safeguards

- Verify the source revision, `core/version`, relevant-source cleanliness, and
  runtime binary version before sending trials.
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
