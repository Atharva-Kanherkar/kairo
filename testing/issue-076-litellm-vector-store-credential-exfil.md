# issue/076-litellm-vector-store-credential-exfil Test Contract

## Functional Behavior

- Run LiteLLM through its real proxy CLI from release `v1.100.0`, commit
  `e4f25265704e2b2c6cf6e81be2e4c5cffff896f4`, with PostgreSQL and master-key
  authentication enabled.
- Create a real `internal_user` and a virtual key explicitly allowed to call
  `/vector_store/new`, `/v1/model/info`, and `openai_routes`.
- Confirm `/v1/model/info` exposes the configured named credential reference,
  but never the credential value.
- Register an internal-user-owned OpenAI-compatible vector store that combines
  the server credential reference with a caller-controlled loopback `api_base`.
- Search that store three times. The capture upstream must receive the synthetic
  server provider credential on all three requests.
- Run a three-request control with the same user, route, query, provider, and
  `api_base`, changing only the credential source to a caller-owned synthetic
  key. The capture upstream must receive the caller key 3/3 and the server key
  0/3.
- Save literal, sanitized client request and response bytes plus literal,
  sanitized forwarded request and response bytes under `transcripts/076/`.
- Generate all keys and credential canaries at runtime. Refuse to write evidence
  until every capture has been checked for unsanitized canaries.
- Record current upstream searches and distinguish this persisted managed-vector
  path from GHSA-3cv6-jpf6-8222's patched request-body routing path.

## Unit Tests

- `outbound_request_omits_secret_flags_provider_credential` reports a violation
  when a raw forwarded HTTP request contains the server credential marker.
- `outbound_request_omits_secret_accepts_caller_control` reports conformance for
  the matched control request.
- The checker rejects empty or malformed HTTP captures instead of passing
  vacuously.
- Python tests cover exact credential replacement, refusal to persist an
  unsanitized canary, HTTP parsing, and result-count validation.

## Integration / Functional Tests

- Kairo conformance tests load exactly three exploit and three control forwarded
  requests.
- Every exploit request is HTTP `POST` to the expected vector-store search path
  and contains only the sanitized server credential marker.
- Every control request is the same route and shape, contains only the sanitized
  caller credential marker, and omits the server marker.
- Captured client responses are HTTP 200 for all six searches.
- The reproduction summary identifies the authenticated caller as
  `internal_user`, records the allowed routes, and reports 3/3 exploit and 3/3
  control results.

## Smoke Tests

- `python3 -m unittest discover -s transcripts/076 -p 'test*.py'` passes.
- `cargo test --workspace` passes.
- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets -- -D warnings` passes.
- `python3 tools/update-readme-counts.py --check` passes.
- A repository-wide credential-shape scan finds no unsanitized reproduction
  canary or fixed API key.

## E2E Tests

- From a clean LiteLLM checkout at the pinned commit, install the locked Python
  3.11 proxy environment, generate Prisma, start an isolated PostgreSQL 17
  database, and run `transcripts/076/reproduce.py` against the real CLI.
- The runner starts its own loopback capture upstream, performs the authenticated
  setup and six searches, validates all counters before replacing committed
  fixtures, and shuts down child processes.
- Allow up to 300 seconds for LiteLLM's bounded Prisma preparation and migration
  step. On startup failure, emit only a bounded log tail after replacing every
  synthetic credential canary.
- Re-run against current `v1.101.0-rc.2`, `v1.102.0-dev.1`, or staging when
  practical. Static current-source evidence alone must be described as static,
  not as a dynamic reproduction.

## Manual / cURL Tests

- The issue writeup must provide sanitized setup and request examples using
  environment variable names only.
- Manual review must verify that removing only `litellm_credential_name` and
  using the caller-owned control key changes the forwarded Authorization value.
- No upstream advisory, email, public issue, or Kairo pull request is sent until
  the full gates pass and the user approves the outward action.
