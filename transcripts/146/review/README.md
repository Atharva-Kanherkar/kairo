# 146 independent review captures

Raw HTTP request and response bytes from the independent rerun on 2026-11-14.

- Target: LiteLLM `6abacd329b70dc668f69c705eb7118fbd639cd96` (upstream main,
  2026-11-13), real proxy CLI on `127.0.0.1:4109`, real PostgreSQL 16, master key
  from `LITELLM_MASTER_KEY`.
- Sanitization: the master key is replaced by `[REDACTED:credential]` in every
  `Authorization` header. Nothing else is redacted.

## `cold-and-warmed/`

One proxy process. The first guardrail request is the first request after
`/health/liveliness`, so both lazy routers load together.

- `*-guardrails-cold-anonymous-{0..4}`: 401, 5/5.
- `*-guardrails-cold-master-{0..4}`: 200 `pending_review`, then 400
  `already exists` 4/4. Both come from the real guardrail handler.
- `012-mcp-anything-*`: `POST /anything/register` returns the MCP dummy client,
  so the MCP route is live.
- `*-guardrails-mcp-warmed-anonymous-{0..4}`: 401, 5/5.
- `*-guardrails-mcp-warmed-master-{0..4}`: 400 `already exists`, 5/5.

## `mcp-first/`

A fresh proxy process where the first request after readiness is
`POST /anything/register`, so the MCP router loads before the guardrails router.
This is the only load order that could put the MCP route first.

- `043-first-mcp-*`: MCP dummy client, 200.
- `044` to `048` `anon-*`: anonymous `POST /guardrails/register`, 401, 5/5.
- `049-master-0-*`: 200 `pending_review` from the real guardrail handler.
- `050-anything-after-*`: MCP dummy client still served, 200.
- `results.json`: the summary the probe script printed.
