# Sweep candidates

Run `20260817T112839Z`. These are non-clean cells with no matching kairo issue. They are leads, not findings.

Nothing here is auto-filed. This repo's value is that every issue folder is a hand-verified claim with a control and a determinism count, and a generated writeup would undercut exactly that. The sweep's job is to hand you a ranked list and the frozen bytes; the writeup is still yours.

Per CONTRIBUTING, one bug per PR. Promote these one at a time.


## Ranked

| gateway | field | verdict | runs | control | evidence |
|---|---|---|---:|---|---|
| bifrost | `stream` | REJECTED | 5/5 | not probed | `` |
| litellm | `thinking (adaptive)` | DROPPED | 5/5 | not probed | `transcripts/sweep/20260817T112839Z/litellm--req-thinking-adaptive.jsonl` |
| litellm | `finish_reason length -> max_tokens` | MANGLED | 5/5 | not probed | `transcripts/sweep/20260817T112839Z/litellm--resp-finish_reason-length.jsonl` |
| litellm | `content_filter signal survives` | MANGLED | 5/5 | not probed | `transcripts/sweep/20260817T112839Z/litellm--resp-finish_reason-content_filter.jsonl` |
| bifrost | `anthropic-beta forwarded or mapped` | DROPPED | 5/5 | not probed | `transcripts/sweep/20260817T112839Z/bifrost--header-anthropic_beta.jsonl` |
| bifrost | `usage counts survive translation` | MANGLED | 5/5 | not probed | `transcripts/sweep/20260817T112839Z/bifrost--resp-usage.jsonl` |
| litellm | `output_config.effort` | DROPPED | 5/5 | not probed | `transcripts/sweep/20260817T112839Z/litellm--req-output_config-effort.jsonl` |
| litellm | `tools[].strict` | DROPPED | 5/5 | not probed | `transcripts/sweep/20260817T112839Z/litellm--req-tools-strict.jsonl` |
| litellm | `anthropic-beta forwarded or mapped` | DROPPED | 5/5 | not probed | `transcripts/sweep/20260817T112839Z/litellm--header-anthropic_beta.jsonl` |
| litellm | `usage counts survive translation` | MANGLED | 5/5 | not probed | `transcripts/sweep/20260817T112839Z/litellm--resp-usage.jsonl` |

## Known defects that came back clean

Each of these is either fixed upstream since it was frozen, or the rig is not exercising the path it used to. Both need a human look, and the second is more likely on a first run.

| gateway | field | issue |
|---|---|---|
| litellm | `image (base64)` | 012 |
| litellm | `tool_result.is_error` | 006 |
| litellm | `thinking block in history` | 016 |
| litellm | `client Authorization must not leak` | 020 |
| litellm | `openai-organization must not leak` | 026 |
| litellm | `no invented empty text block` | 009 |
| litellm | `upstream tool_call id reaches the client` | 004 |
| bifrost | `tool_use.id round trip` | 037 |

