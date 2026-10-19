# 105, Bifrost drops message-level `cache_control` on Chat Completions, so opencode on Claude never hits the prompt cache

- **Upstream**: [maximhq/bifrost](https://github.com/maximhq/bifrost). No issue or
  pull request reports this on 2026-10-19. Related, different defects:
  [#2469](https://github.com/maximhq/bifrost/issues/2469) (clients that send no
  marker, answered by the opt-in `prompt_cache.auto_inject`) and
  [#3942](https://github.com/maximhq/bifrost/issues/3942) (block-level markers
  stripped on OpenRouter, fixed; the block-level control below passes)
- **Tool under test**: Bifrost HTTP transport, latest release
  [`transports/v2.3.0`](https://github.com/maximhq/bifrost/releases/tag/transports%2Fv2.3.0)
  at `9237ec06672406e437b3cb33fc793ffd0403b3ae` (clean VCS build, `vcs.modified=false`)
  with the published core `v1.12.0` (`2a4c51b2cc25f995bb8d20825fe7a34be902ae60`).
  The same struct is unchanged on the default branch `dev` at `1813de533`
  (2026-10-19)
- **Client**: opencode `1.19.3` (latest release, 2026-10-16) and `1.18.31`, with the
  `@ai-sdk/openai-compatible` provider pointed at Bifrost's `/openai` route, the
  configuration Bifrost's docs give and `bifrost run opencode` generates
- **Reproduced**: 2026-10-19, 03:08Z to 03:20Z, macOS arm64. Live
  `openrouter/anthropic/claude-haiku-4.5` through Bifrost's `openrouter` provider,
  and `anthropic/claude-haiku-4-5` through Bifrost's `anthropic` provider with the
  forwarded request recorded (the Anthropic account had no credit at capture time, so
  that leg proves the forwarded bytes only; see [Limits](#limits)). Recording relays on
  every hop
- **Label**: `bug`

## Claim

A Chat Completions client can mark a message for Anthropic prompt caching with
`"cache_control": {"type": "ephemeral"}` on the message itself or on one of its
content blocks. Bifrost keeps the block-level form and silently discards the
message-level form, because `schemas.ChatMessage` has no `cache_control` field.
The provider then receives no breakpoint, and Anthropic models cache only at
explicit breakpoints, so every turn is billed at the uncached input rate.

opencode, one of the four coding harnesses Bifrost documents, sends exactly the
message-level form for Claude models. Through Bifrost it gets zero cache reads on
every turn; the same opencode build sent straight to OpenRouter reads about 28,000
cached tokens on every turn after the first.

## What breaks

An opencode user on a Claude model behind Bifrost, set up the documented way:

1. `bifrost run opencode --model anthropic/claude-sonnet-4-6`, or the manual
   `opencode.json` in Bifrost's `docs/cli-agents/opencode.mdx`, registers a provider
   with `"npm": "@ai-sdk/openai-compatible"` and `"baseURL": ".../openai"`.
2. For a model whose id contains `claude`, opencode's `applyCaching`
   (`packages/opencode/src/provider/transform.ts:358` at
   [`v1.19.3`](https://github.com/anomalyco/opencode/blob/v1.19.3/packages/opencode/src/provider/transform.ts#L358))
   marks the first two system messages and the last two non-system messages. On this
   provider the SDK writes the marker as a message property. Every one of the 30
   requests in the recorded sessions carries it that way: system and tool-result
   messages on tool turns (20), system and user messages on first turns (10).
3. Bifrost parses the body into `ChatMessage`, which has `name`, `role`, `content`
   and the tool or assistant fields only, and the marker is gone. The request
   Bifrost sends to Anthropic or OpenRouter carries no `cache_control` at all.
4. The provider caches nothing. The session works, so nothing tells the user; the
   bill does.

Nothing fails visibly. This is a cost and latency defect on a supported, default
path, not a halt.

## Measured impact

All live numbers are OpenRouter's own `usage.cost` and `cached_tokens` from the
recorded upstream responses.

Same captured opencode turn (about 28,000 prompt tokens), five trials, each trial
with a fresh nonce in the system text, sent twice. The second call can only read
what the first wrote:

| Path | Second-call cached tokens | Second-call cost | N |
|---|---|---|---|
| Bifrost, opencode's message-level markers | 0 | $0.02811 to $0.02812 | 5/5 |
| Bifrost, same markers moved onto content blocks (trigger removed) | 28,032 to 28,035 | $0.00289 | 5/5 |
| Direct to OpenRouter, opencode's message-level markers | 27,891 to 27,895 | $0.00323 | 5/5 |

That is 8.7 times the input cost of a turn whose prefix should have come from the
cache.

Real opencode 1.19.3, five sessions per arm, same five-step task (create a file, read
it, edit it, run `wc -l`), `openrouter/anthropic/claude-haiku-4.5`:

| Arm | Upstream calls | Prompt tokens | Cached tokens | Billed |
|---|---|---|---|---|
| Through Bifrost | 30 | 714,163 | 0 | $0.7272 |
| Direct to OpenRouter | 33 | 807,154 | 656,993 | $0.2675 |

Every session finished its task in both arms. Per upstream call that is $0.0242
against $0.0081, three times the cost on five-step sessions. **Inferred, not
measured**: the ratio grows with session length, toward the 8.7 times of the replay
above, because each later turn re-sends a longer cached prefix. Time to first token
on uncached turns is also longer; it was not measured here.

The earlier live runs straight to Anthropic (opencode 1.18.31 through Bifrost's
`anthropic` provider, before the account ran out of credit) show the same thing in
opencode's own accounting: cache write 0 and read 0 on all 20 steps of three
sessions ([`supplementary-native-anthropic/`](../../transcripts/105/supplementary-native-anthropic/)).
Those runs were not wire-captured.

## Wire evidence

Everything is under [`transcripts/105/`](../../transcripts/105/), raw HTTP per hop,
tenant headers redacted by [`rig/sanitize_copy.py`](../../transcripts/105/rig/sanitize_copy.py).
The case index with call-by-call usage is
[`wire/results.json`](../../transcripts/105/wire/results.json).

Client to Bifrost (`wire/client-to-bifrost/exchange-001-request.http`), abridged:

```json
{"model": "anthropic/claude-haiku-4-5",
 "messages": [
  {"role": "system", "content": "[trial ...]\nYou are OpenCode, ...", "cache_control": {"type": "ephemeral"}},
  {"role": "user", "content": "\"Create notes.txt containing the line alpha. ...\"", "cache_control": {"type": "ephemeral"}}],
 "tools": [...], "stream": true}
```

Bifrost to Anthropic (`wire/bifrost-to-anthropic/exchange-001-request.http`), abridged:

```json
{"model": "claude-haiku-4-5",
 "system": "[trial ...]\nYou are OpenCode, ...",
 "messages": [{"role": "user", "content": [{"type": "text", "text": "\"Create notes.txt ...\""}]}],
 "tools": [...], "stream": true}
```

Counted over every recorded body, client to provider:

| Case | Client markers per body | Forwarded markers per body | N |
|---|---|---|---|
| `anthropic`, message-level | 2 | 0 | 10/10 |
| `anthropic`, block-level control | 2 | 2 | 10/10 |
| `openrouter`, message-level | 2 | 0 | 10/10 |
| `openrouter`, block-level control | 2 | 2 | 10/10 |
| opencode sessions through Bifrost (`opencode/capture/`) | 2 or 3, all message-level | 0 | 30/30 |

## Root cause

`core/schemas/chatcompletions.go` in core `v1.12.0`, line 1114:

```go
type ChatMessage struct {
	Name    *string             `json:"name,omitempty"`
	Role    ChatMessageRole     `json:"role,omitempty"`
	Content *ChatMessageContent `json:"content"`
	*ChatToolMessage
	*ChatAssistantMessage
}
```

`cache_control` exists on `ChatContentBlock` (line 1322) and on tools (line 436), not
on the message, so a message-level marker is dropped when the request is decoded,
before any provider converter or the prompt-cache plugin runs. Unchanged on `dev`
`1813de533`. Bifrost already reads message-level markers elsewhere: Bedrock
InvokeModel request parsing applies them to the message content
(`applyMessageContentCacheControl`, see
[#7336](https://github.com/maximhq/bifrost/issues/7336)). It is also adding markers to
more Chat Completions positions (`cache_control` on `tool_calls`,
[#8494](https://github.com/maximhq/bifrost/pull/8494)).

## Controls

- **Trigger removed**: the identical request with each message-level marker moved
  onto the message's content block is forwarded with both markers and caches, 10/10
  bodies and 5/5 live second calls.
- **Gateway removed**: opencode's exact message-level request sent straight to
  OpenRouter caches, 5/5 replays and 5/5 real sessions (20+ cached steps).
- **Same client, same model, same task**: the opencode arms differ only in
  `baseURL`.

So the provider and the client both handle the message-level form; the marker is
lost inside Bifrost.

## Operator workaround and why it is partial

Bifrost's `prompt_cache.auto_inject` is off by default
(`docs/features/prompt-caching.mdx`: "Off by default"). The per-request header
`x-bf-prompt-cache-auto-inject: true` alone injected nothing in this build, with or
without client markers ([`auto-inject/`](../../transcripts/105/auto-inject/): the
first four Anthropic and the first two OpenRouter forwarded requests carry 0
markers). With `"prompt_cache": {"auto_inject": true}` set on the provider, Bifrost
forwards one marker, on the first system block, for opencode's request (Anthropic
exchanges 007 and 008, OpenRouter exchange 011). That caches the tools and system prefix but not the
conversation the client asked to cache, so it reduces the loss and does not remove
it.

## Bug or not

- **Expected behavior is the intended spec**: Bifrost's prompt-caching page says
  "Caller markers always win - a request that already carries `cache_control` ... is
  forwarded unchanged", and Bifrost ships an opencode integration whose generated
  config produces exactly these requests. OpenRouter accepts the same
  message-level form and caches on it.
- **Maintainer ruling**: none found. #2469 was decided for clients that send no
  marker; this client sends one. No comment, denylist or commit classifies
  message-level `cache_control` as unsupported.
- **Supported usage with default settings**: yes. `bifrost run opencode`, default
  provider config, a Claude model.
- **Boundary**: not a disclosure claim. The consequence is cost and latency.
- **Fix a maintainer would ship**: accept `cache_control` on `ChatMessage` and,
  when converting for a provider that takes block-level markers, place it on the
  message's last content block (promoting string content to one text block, as the
  auto-inject path already does).
- **Label**: `bug`

## Test

Checker `client_cache_breakpoints_reach_provider` in
`crates/harness/src/checks.rs`. Invariant: the forwarded request carries at least
as many provider-native cache breakpoints (`cache_control`, `cachePoint`,
`prompt_cache_breakpoint`, anywhere) as the client asked for, counted per marked
message or tool at either level and capped at Anthropic's four. It counts rather
than matching positions, so any placement a fix chooses passes, and it refuses a
client request with no marker.

Conformance tests in `crates/harness/tests/conformance.rs`:

- `bug105_message_level_cache_control_never_reaches_the_provider`: 20 message-level
  pairs violate, 20 block-level control pairs conform, both providers.
- `bug105_dropped_breakpoints_bill_every_turn_uncached`: reads the recorded usage;
  the second call reads 0 cached tokens through Bifrost (5/5) and more than 20,000
  in both controls (5/5 each).
- `bug105_opencode_sessions_through_bifrost_never_cache`: every marked request of
  the five real opencode sessions violates the checker and every upstream response
  reports 0 cached tokens; the five direct sessions read the cache on at least 20
  calls.

## Reproduce

Rig in [`transcripts/105/rig/`](../../transcripts/105/rig/). Environment variable
names only: `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY` (loaded from a local `.env`).

```bash
# Bifrost v2.3.0 binary built at 9237ec06 (core v1.12.0); config routes the
# anthropic and openrouter providers through recording relays.
cp transcripts/105/rig/bifrost-config.json "$BIFROST_APP_DIR/config.json"
bifrost -app-dir "$BIFROST_APP_DIR" -port 8191 &
transcripts/105/rig/relays.sh "$PWD/ev1"          # 4 relays: client, anthropic, openrouter, direct
ROOT="$PWD/ev1" python3 transcripts/105/rig/live_matrix.py
# real opencode sessions (OPENCODE_BIN points at opencode 1.19.3)
transcripts/105/rig/relays.sh "$PWD/ev2" && transcripts/105/rig/oc-matrix.sh
cargo test -p kairo --test conformance bug105
```

`oc.sh`, `batch.sh` and `run-bf.sh` hold paths from the recording machine; adjust
them before reuse.

## Limits

- The Anthropic account had no credit on 2026-10-19, so the native `anthropic`
  provider leg returns `400` after the forwarded request is recorded. Its forwarded
  bytes carry no marker 10/10, the live cost effect was measured through OpenRouter
  (which serves the same Claude model with the same explicit-breakpoint caching),
  and the earlier native runs show 0 cache reads in opencode's accounting. A live
  native Anthropic replay is still owed.
- Bedrock and Vertex were not tested. Because the marker is lost when the request
  is decoded, they are expected to behave the same; that is inference.
- Claude Code and Codex are not affected: Claude Code uses the Anthropic route with
  block-level markers, Codex uses Responses. The defect is specific to Chat
  Completions clients that use the message-level form.
