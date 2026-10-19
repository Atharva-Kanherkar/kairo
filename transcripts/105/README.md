# 105 transcripts

Bifrost `transports/v2.3.0` (`9237ec06`, core `v1.12.0`) on `127.0.0.1:8191`,
recorded on 2026-10-19 between 03:08Z and 03:20Z. Every hop goes through
[`rig/record_proxy.py`](rig/record_proxy.py), which writes the raw request and the
raw response for each exchange. Request `Authorization` and `x-api-key` values were
written as `<redacted>` at capture time; [`rig/sanitize_copy.py`](rig/sanitize_copy.py)
also redacts response tenant headers (`anthropic-organization-id`,
`anthropic-workspace-id`, `request-id`, `traceresponse`, `CF-RAY`, `set-cookie`,
`X-Generation-Id`) and Anthropic `request_id` values in bodies. Bodies are otherwise
byte-for-byte as recorded.

| Directory | What it holds |
|---|---|
| `wire/` | [`rig/live_matrix.py`](rig/live_matrix.py): five trials of five cases, each trial sends the same captured opencode turn twice with a fresh nonce. `results.json` maps each call to its exchange files per hop and records the usage seen by the client |
| `wire/client-to-bifrost/` | what the client sent Bifrost (`/openai/v1/chat/completions`) |
| `wire/bifrost-to-anthropic/` | what Bifrost forwarded to `api.anthropic.com` (`400`, account without credit; the request bytes are the evidence) |
| `wire/bifrost-to-openrouter/` | what Bifrost forwarded to `openrouter.ai`, and the live answers |
| `wire/client-to-openrouter-direct/` | the gateway-removed control, straight to OpenRouter |
| `opencode/capture/` | five real opencode 1.19.3 sessions through Bifrost and five direct, same hops as above |
| `opencode/runs/` | each session's `opencode run --format json` event log and its `opencode.json` (the recorded runs wrote the key into the file and it was redacted on copy; the current [`rig/oc.sh`](rig/oc.sh) writes `{env:OC_KEY}` instead) |
| `supplementary-native-anthropic/` | three earlier opencode 1.18.31 sessions through Bifrost's `anthropic` provider against live Anthropic, event logs only, not wire-captured |
| `auto-inject/` | the `prompt_cache.auto_inject` checks: the per-request header first, then `auto_inject` set on the provider ([`rig/bifrost-config-auto-inject.json`](rig/bifrost-config-auto-inject.json)); exchange map below |

`auto-inject/` exchanges. Client hop: 001 and 002 Anthropic with message-level
markers and the header; 003 and 004 OpenRouter, same; 005 no markers with the header;
006 no markers, no header; Bifrost restarted with `auto_inject` on both providers; 007
message-level, 008 no markers, 009 OpenRouter message-level. Anthropic hop: 001 to 004
are client 001, 002, 005 and 006 (0 markers each); 005, 006, 009 and 010 are Bifrost's startup model-listing `GET`s, 007 and 008 carry 1 marker.
OpenRouter hop: 001 and 002 carry 0 markers, 011 carries 1, the rest are startup
`GET`s.

Request bodies used by the matrix: [`rig/req-oc-turn.json`](rig/req-oc-turn.json)
(an opencode request as captured, markers on the messages) and
[`rig/req-block-level.json`](rig/req-block-level.json) (the same request with each
marker moved onto the message's content block, nothing else changed).
