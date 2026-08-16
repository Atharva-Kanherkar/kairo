# 037, Bifrost sanitizes an upstream tool-call id and never restores it

- **Upstream**: no ticket found. Same class as kairo 005, the Switchyard tool-id
  sanitizer: a gateway rewrites an id to satisfy a downstream contract and has no
  inverse, so the multi-turn loop breaks.
- **Tool under test**: Bifrost gateway **v1.6.11**, `npx -y @maximhq/bifrost`.
- **Reproduced**: 2026-08-16, offline capture rig (`transcripts/bifrost-rig/`),
  no provider keys. **5/5**.

## What breaks

The rewrite itself is correct and worth keeping. An upstream emits a tool-call id
containing characters Anthropic's `tool_use.id` contract does not allow:

```
call/with+punct=and.dots:1
```

Bifrost hands the client a conformant id instead:

```
8d0701f3952e112c_call_with_punct_and_dots_1
```

Good — the raw id fails `^[A-Za-z0-9_-]{1,64}$` and the sanitized one satisfies it.
The bug is what happens next. The client does the only thing the protocol permits:
it echoes that id back as `tool_use.id` and `tool_result.tool_use_id` on the next
turn. Bifrost forwards **the sanitized id** upstream.

The upstream never issued `8d0701f3952e112c_...`. It issued
`call/with+punct=and.dots:1`. Against any provider that validates that a submitted
call id matches one it handed out, the tool result is unmatchable and the
conversation cannot continue past its first tool call.

The rewrite is one-directional: it is applied on the response and not inverted on
the request.

## Wire evidence

`transcripts/037/roundtrip.json`:

```json
{"upstream_original_id": "call/with+punct=and.dots:1",
 "client_received_id":  "8d0701f3952e112c_call_with_punct_and_dots_1",
 "ids_sent_upstream_turn2": ["8d0701f3952e112c_call_with_punct_and_dots_1",
                             "8d0701f3952e112c_call_with_punct_and_dots_1"]}
```

`transcripts/037/upstream-request-turn2.jsonl` is the turn-2 request in capture
format. Both `call_id` slots — the `function_call` and its `function_call_output` —
carry the sanitized form. The original appears nowhere, 5/5.

The hash prefix suggests the mapping is derived rather than remembered, so nothing
on the request path knows what the id was before it was rewritten.

## Root cause

Not pinned to a line. Localized to the pair of translations: the response mapper
sanitizes the upstream id for the client, and the request mapper forwards client
ids verbatim. Neither is wrong alone; together they lose the correspondence.

## Confidence

| Claim | Confidence | Basis |
|---|---|---|
| The sanitized id is what goes back upstream | **High** | Capture is ground truth, 5/5 |
| The rewrite is deterministic | **High** | Identical id across all 5 runs |
| The sanitized id satisfies the id contract | **High** | Asserted directly with `id_conforms` |
| A live provider would reject the mismatched id | **Untested** | Offline mock accepts anything |
| Whether ids needing no rewrite round-trip cleanly | **Confirmed unaffected** | 030/031 fixtures show verbatim ids |
| Named source location | **Not established** | Behavioural isolation only |

The severity claim depends on a provider validating call ids, which this rig cannot
test. What is proven here is the mismatch itself.

## How real the bug is

Real but conditional, and worth stating precisely. Against a provider that ignores
unknown call ids, nothing breaks. Against one that validates them, every multi-turn
tool conversation fails at the second turn — and only for upstreams whose ids
contain characters needing sanitization, which is exactly the population the
sanitizer exists to serve. So the feature that makes those providers usable is also
what breaks them one turn later.

The failure would surface as an upstream 400 on turn two of a working conversation,
with the caller having sent precisely the id the gateway told it to send.

## Test

`bifrost_does_not_restore_sanitized_toolcall_id` (the frozen violation, using the
new `toolcall_id_restored_upstream` checker) and
`bifrost_sanitized_id_is_charset_clean_for_the_client`, which asserts with
`id_conforms` that the rewrite achieves what it set out to — the finding is the
missing inverse, not the sanitizer.

Invariant: *when a gateway rewrites an upstream tool-call id, the id it sends back
upstream is the one the upstream issued.*

## Reproducing

```bash
cd transcripts/bifrost-rig
python3 capture_upstream.py &
npx -y @maximhq/bifrost -app-dir . -port 8080 &
python3 hunt.py
```
