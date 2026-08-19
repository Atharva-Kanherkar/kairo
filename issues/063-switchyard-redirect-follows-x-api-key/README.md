# 063, Switchyard follows HTTP redirects with Anthropic and Google API keys

- **Upstream**: no public ticket. NVIDIA `SECURITY.md` asks for mail to
  `psirt@nvidia.com` rather than a GitHub issue. Credential leakage is
  in scope. Adjacent: they already disable redirects on the
  `forward_auth` HTTP client because "A redirect could move
  provider-specific headers to another origin." The default
  `api_key_env` client still follows redirects.
- **Tool under test**: Switchyard `switchyard-server` 0.2.0 (kairo
  binary, 2026-08-12) and `origin/main` at `6babb3b` (2026-08-19
  local build).
- **Not a credential incident**: probes used fake canaries. No real
  keys. No rotation needed.
- **Reproduced**: 2026-08-19. Anthropic `x-api-key` on the redirect
  sink 5/5 (0.2.0 extra_headers) and 3/3 (`main` `api_key_env`).
  Gemini `x-goog-api-key` extra header on the sink 1/1 both builds.
  OpenAI `Authorization: Bearer` is stripped on the same 307. Evidence:
  `transcripts/063/`.

## What breaks

Switchyard's default `reqwest` client follows HTTP 307 to another
origin. Reqwest strips `Authorization` on that hop. It does not strip
`x-api-key` or `x-goog-api-key`.

Anthropic auth is `x-api-key`, not Bearer. A 307 from the configured
`base_url` to a different host therefore carries the Anthropic
deployment key. Gemini deployments that put the Google key in
`extra_headers.x-goog-api-key` (common next to a Bearer) leak that
header the same way, even while the Bearer is dropped.

Who that hurts:

- Any Anthropic (or Anthropic-compatible) target whose `base_url`
  307/308s off-origin: corporate reverse proxies, HTTP to a different
  host, an open redirect on the first hop, a compromised first hop.
  The second origin gets `x-api-key` and can call Anthropic as the
  operator.
- Gemini `extra_headers.x-goog-api-key`: the Google key arrives at the
  redirect target; the OpenAI-shaped Bearer does not. Same process,
  two headers, only one is protected.
- Shared Switchyard: a caller who can make the configured upstream
  return a 307 (or who waits for a misconfigured gateway to do it)
  collects the office key. HTTP 200. No error to the client.

The `forward_auth` path already refuses to follow redirects. That is
the control that shows they know this class. `api_key_env` and
`extra_headers` were left on the following client.

```mermaid
flowchart LR
  caller["caller POST /v1/messages"] --> sy["Switchyard default reqwest client"]
  sy -->|"POST, x-api-key"| origin["configured base_url"]
  origin -->|"307 Location=http://sink/..."| sy
  sy -->|"follows, still holding x-api-key"| sink["attacker-controlled origin"]
```

## Wire evidence

Three legs.

1. **Switchyard Anthropic**
   - 0.2.0, `extra_headers.x-api-key = CANARY_ANTHROPIC_X_API_KEY`,
     origin `127.0.0.1:19220` 307 to `127.0.0.1:19221`. Sink
     `x-api-key` is the canary. HTTP 200. 5/5.
     `transcripts/063/v020-anth-307-sink.jsonl`.
   - `origin/main` `6babb3b`, `api_key_env` (not extra_headers). Sink
     `x-api-key = CANARY_ANTHROPIC_API_KEY_ENV`. HTTP 200. 3/3.
     `transcripts/063/main-anth-307-sink.jsonl`.
2. **Control: OpenAI Bearer on the same 307 shape**
   - `origin/main`, `api_key_env` Bearer. Origin hop has
     `Authorization: Bearer CANARY_OPENAI_API_KEY_ENV`. Sink hop has
     no `Authorization`. HTTP 200.
     `transcripts/063/main-openai-307-sink-control.jsonl`.
   - Same process, Gemini extra header: origin has Bearer and
     `x-goog-api-key`. Sink has `x-goog-api-key` only.
     `transcripts/063/main-goog-307-origin.jsonl`,
     `transcripts/063/main-goog-307-sink.jsonl`.
3. **Determinism**
   - Distinct ports, so the Location is a different origin.
   - 0.2.0 extra_headers and `main` `api_key_env` both leak. This is
     the header name, not one config spelling.

## Root cause (in Switchyard source)

`crates/libsy-llm-client/src/client.rs` (`origin/main`):

- Default client: `reqwest::Client::builder()` (follows redirects).
- `forward_auth` client: `.redirect(Policy::none())` with the comment
  that a redirect could move provider-specific headers to another
  origin.
- `Backend::apply_auth` sets Anthropic `x-api-key`. OpenAI uses
  `bearer_auth` (`Authorization`). Reqwest treats Authorization as
  sensitive on cross-origin redirects. `x-api-key` and
  `x-goog-api-key` are ordinary headers.

`apply_extra_headers` copies TOML `extra_headers` onto the same
following client, so a Gemini `x-goog-api-key` extra header takes the
same path.

## Test

`upstream_omits_header_value` on the sink capture. The invariant: a
cross-origin follow MUST NOT still hold the provider key.

- `switchyard_redirect_sink_keeps_anthropic_x_api_key` (violation, `api_key_env`)
- `switchyard_redirect_sink_keeps_anthropic_extra_header` (violation, 0.2.0 extra_headers spelling)
- `switchyard_redirect_sink_keeps_goog_extra_header` (violation)
- `switchyard_redirect_sink_strips_openai_bearer` (control)

## How to reproduce

Needs a local `switchyard-server` (kairo 0.2.0 binary or a current main
build). Canaries only. No provider keys.

```bash
# from the kairo repo root
export SWITCHYARD_BIN=/path/to/switchyard-server
./transcripts/063/repro.sh
```

Expected on stdout:

```
=== Anthropic api_key_env 307 (expect sink x-api-key canary) ===
http 200
sink x-api-key CANARY_ANTHROPIC_API_KEY_ENV
=== OpenAI Bearer 307 control (expect sink Authorization absent) ===
http 200
sink authorization None
=== Gemini extra_headers x-goog-api-key 307 (expect sink goog canary, no Bearer) ===
http 200
sink authorization None
sink x-goog-api-key CANARY_GOOG_EXTRA_HEADER
```

What the script starts, per provider:

1. `redirect_pair.py` sink on one port (records request headers, returns a canned 200).
2. `redirect_pair.py` origin on another port (records, replies HTTP 307 to the sink). Distinct ports, so the Location is a different origin.
3. `switchyard-server` with the matching toml (`sy-anth.toml`, `sy-openai.toml`, `sy-goog.toml`). `base_url` is the origin.
4. One `curl` into Switchyard. The frozen checkers then look at the sink capture.

Manual Anthropic-only variant (same bytes as `repro.sh`'s first cell):

```bash
python3 transcripts/063/redirect_pair.py sink 19321 transcripts/063/repro-anth-sink.jsonl &
python3 transcripts/063/redirect_pair.py origin 19320 transcripts/063/repro-anth-origin.jsonl \
  http://127.0.0.1:19321/v1/messages 307 &
export SY_HUNT_ANTH_KEY=CANARY_ANTHROPIC_API_KEY_ENV
"$SWITCHYARD_BIN" --config transcripts/063/sy-anth.toml --host 127.0.0.1 -p 19322 &
curl -sS http://127.0.0.1:19322/v1/messages \
  -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
  -d '{"model":"claude-hunt","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}'
# transcripts/063/repro-anth-sink.jsonl headers.x-api-key is CANARY_ANTHROPIC_API_KEY_ENV
```

OpenAI control toml: `transcripts/063/sy-openai.toml`. Gemini extra header:
`transcripts/063/sy-goog.toml`. Generated `repro-*-{sink,origin}.jsonl` files
are local and gitignored.

Frozen checkers (no Switchyard binary required):

```bash
cargo test --test conformance switchyard_redirect
```
