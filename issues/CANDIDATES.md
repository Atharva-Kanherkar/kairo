# Sweep candidates

Run `20260817T123216Z`. These are non-clean cells with no matching kairo issue. They are leads, not findings.

Nothing here is auto-filed. This repo's value is that every issue folder is a hand-verified claim with a control and a determinism count, and a generated writeup would undercut exactly that. The sweep's job is to hand you a ranked list and the frozen bytes; the writeup is still yours.

Per CONTRIBUTING, one bug per PR. Promote these one at a time.


## Ranked

| gateway | field | verdict | runs | control | evidence |
|---|---|---|---:|---|---|
| litellm | `output_config.effort` | DROPPED | 5/5 | not probed | `transcripts/sweep/20260817T123216Z/litellm--req-output_config-effort.jsonl` |
| litellm | `thinking (adaptive)` | DROPPED | 5/5 | not probed | `transcripts/sweep/20260817T123216Z/litellm--req-thinking-adaptive.jsonl` |
| litellm | `anthropic-beta forwarded or mapped` | DROPPED | 5/5 | not probed | `transcripts/sweep/20260817T123216Z/litellm--header-anthropic_beta.jsonl` |
| litellm | `finish_reason length -> max_tokens` | MANGLED | 5/5 | not probed | `transcripts/sweep/20260817T123216Z/litellm--resp-finish_reason-length.jsonl` |
| litellm | `content_filter signal survives` | MANGLED | 5/5 | not probed | `transcripts/sweep/20260817T123216Z/litellm--resp-finish_reason-content_filter.jsonl` |
| litellm | `usage counts survive translation` | MANGLED | 5/5 | not probed | `transcripts/sweep/20260817T123216Z/litellm--resp-usage.jsonl` |
| bifrost | `anthropic-beta forwarded or mapped` | DROPPED | 5/5 | not probed | `transcripts/sweep/20260817T123216Z/bifrost--header-anthropic_beta.jsonl` |
| bifrost | `usage counts survive translation` | MANGLED | 5/5 | not probed | `transcripts/sweep/20260817T123216Z/bifrost--resp-usage.jsonl` |

## Known defects that came back clean

Each of these is either fixed upstream since it was frozen, or the rig is not exercising the path it used to. Both need a human look, and the second is more likely on a first run.

| gateway | field | issue |
|---|---|---|
| litellm | `image (base64)` | 012 |
| litellm | `tool_result.is_error` | 006 |
| litellm | `thinking block in history` | 016 |
| litellm | `no invented empty text block` | 009 |
| litellm | `upstream tool_call id reaches the client` | 004 |
| bifrost | `tool_use.id round trip` | 037 |

## Security leads (hand-traced)

These are code-traced leads. They do not have wire evidence or an N of N
count, so they are not issue folders. Promote one only after the missing
capture exists.

### LiteLLM CLI SSO: the flow initiator can poll the victim's session

- Target: `BerriAI/litellm` at `27c796c412eed2f7336dccb46a58247463b7c498`
  (2026-11-14).
- Claim: an unauthenticated caller starts a CLI login, sends the victim the
  real proxy login link and the user code, then polls with the secret it
  already owns and receives the victim's LiteLLM JWT.
- Path:
  - `POST /sso/cli/start` returns `login_id`, `poll_secret`, and `user_code`
    (`litellm/proxy/management_endpoints/ui_sso.py:763`).
  - The victim completes
    `/sso/key/generate?source=litellm_cli&key=<login_id>` (`:1140`), which
    binds the victim's SSO session to the caller's `login_id`.
  - The caller polls `/sso/cli/poll/<login_id>` with
    `x-litellm-cli-poll-secret` (`:2734`) and receives the JWT.
- Why the recent fix does not settle it: the poll secret binds the poll to the
  flow initiator, and the initiator is the attacker. The secret does not bind
  the completion to the browser that polls.
- Missing evidence: SSO is enterprise-gated. There is no license in the local
  build, so the start, browser completion, and poll sequence has not been run
  end to end. The local proxy can issue a `login_id` and a poll secret, but it
  cannot complete the SSO callback without a license and an identity provider.
- Upstream: pending huntr report from 2026-07-25, "CLI SSO session fixation in
  /sso/cli/poll leaks victim LiteLLM JWT". This is a possible fix bypass, not
  a novel class.
- Status: NEEDS EVIDENCE. Do not promote to an issue folder until the full
  sequence is captured with 5 of 5 polls and a no-secret control.
