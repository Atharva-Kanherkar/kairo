# 116, LiteLLM MCP true_passthrough masks upstream expired OAuth token 401 as HTTP 200 with empty tools: [], preventing client token refresh

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). Reported in open issue
  [#49223](https://github.com/BerriAI/litellm/issues/49223) (filed 2026-10-30), addressed by open PR
  [#49261](https://github.com/BerriAI/litellm/pull/49261) (filed 2026-10-30). Related:
  [#38208](https://github.com/BerriAI/litellm/issues/38208) (`auth_required` empty tools),
  [#40099](https://github.com/BerriAI/litellm/pull/40099) (connect probe),
  [#48360](https://github.com/BerriAI/litellm/pull/48360) (list failure handling).
  Classified `duplicate-open`.
- **Tool under test**: LiteLLM proxy `v1.106.6` / `1.108.0-dev` at commit
  `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-31), running with local PostgreSQL
  and Streamable HTTP / SSE MCP session transport.
- **Provider**: Deterministic local mock MCP server on `127.0.0.1:19998/mcp` implementing
  JSON-RPC 2.0 MCP protocol methods `initialize`, `notifications/initialized`, and `tools/list`.
- **Clients**: Standard MCP clients over Streamable HTTP (Claude Code, Cursor, Windsurf,
  Kiro CLI, `httpx` 0.28.1).
- **Reproduced**: 2026-11-01, macOS arm64, Python 3.12.13. 5 of 5 runs per case across
  4 differential conditions (20 requests total).
- **Label**: `bug`

## Claim

When an MCP server is configured on LiteLLM with `auth_type: "true_passthrough"`, sending an
expired or invalid upstream OAuth bearer token to a single-server MCP route (such as
`POST /{server}/mcp` or `POST /mcp/{server}`) causes LiteLLM to swallow the upstream HTTP 401
Unauthorized response during `tools/list`. Instead of relaying the 401 challenge with a
`WWW-Authenticate` header to the client, LiteLLM responds with HTTP 200 OK and an empty list of
tools (`"tools": []`), burying the upstream 401 inside an internal metadata object
(`_meta["litellm.ai/server_outcomes"]`).

Because standard MCP clients do not inspect private proprietary `_meta` fields, the client
interprets HTTP 200 OK as a successful connection to a server that has zero tools. The client
never receives an HTTP 401 challenge, never triggers its OAuth token refresh flow, and permanently
runs with zero tools until the user manually wipes their cached credentials.

## What breaks

A developer or autonomous agent system (e.g. Claude Code, Cursor, Windsurf, Kiro CLI) connects to
an OAuth-protected MCP server (such as Datadog, Slack, Jira, or GitHub) proxied through LiteLLM:

1. The client completes the initial OAuth login and receives an access token with a 1-hour TTL
   (`expires_in: 3600`).
2. After 1 hour, the access token expires.
3. The client initiates a new MCP session and sends `initialize` followed by `tools/list` with
   the expired bearer token in the `Authorization` header.
4. The upstream MCP server rejects the expired token with `HTTP 401 Unauthorized` and includes a
   `WWW-Authenticate: Bearer error="invalid_token", error_description="The access token expired"`
   challenge header per RFC 6750 / RFC 9728.
5. LiteLLM's `get_filtered_server_tools` in `catalog.py` catches `MCPUpstreamAuthError`, absorbs
   it, and returns `ListToolsResult(tools=[])`.
6. LiteLLM emits `HTTP 200 OK` with JSON-RPC body:
   `{"jsonrpc":"2.0","id":1,"result":{"_meta":{"litellm.ai/server_outcomes":{"test_server":{"status":"auth_required","http_status":401}}},"tools":[]}}`.
7. The MCP client reads HTTP 200 OK and an empty `tools` list. Because there is no 401 status and
   no `WWW-Authenticate` header, the client's OAuth layer never triggers token refresh.
8. The agent session proceeds with zero tools available, immediately failing all downstream tool-call
   operations and requiring manual user intervention to delete cached session tokens.

## Bug or not

Before classifying this finding as a bug, the five usefulness questions from `AGENTS.md` are evaluated:

- **Is the expected behavior really the spec?** Yes. Under RFC 6750 section 3 and RFC 9728, an expired
  or invalid bearer token must be answered with HTTP 401 Unauthorized and a `WWW-Authenticate` header.
  In MCP, single-server endpoints act as direct proxies for the underlying server. Returning HTTP 200 OK
  with an empty tool array violates the HTTP and OAuth contracts.
- **Have maintainers already ruled on it?** Yes. Upstream maintainers accepted issue
  [#49223](https://github.com/BerriAI/litellm/issues/49223) as a bug, and open PR
  [#49261](https://github.com/BerriAI/litellm/pull/49261) implements the fix.
- **Is the trigger supported usage?** Yes. `auth_type: "true_passthrough"` is a standard, documented
  LiteLLM configuration for transparently forwarding user credentials to MCP backends.
- **Is a real boundary crossed?** Yes. The gateway absorbs an authentication error across the client
  boundary, transforming an explicit authentication rejection into a fake successful response with
  empty content.
- **What fix would a maintainer ship?** On single-server MCP routes, propagate upstream HTTP 401
  authentication challenges to the client with `WWW-Authenticate` instead of masking them as HTTP 200
  with empty `tools: []`.

Record label: `bug`.

## Wire evidence

### Direct upstream with expired token (`upstream.http`)

The upstream MCP server correctly rejects the expired token with HTTP 401 and challenge header:

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
WWW-Authenticate: Bearer error="invalid_token", error_description="The access token expired"

{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32000,
    "message": "Token expired"
  }
}
```

### Observed from LiteLLM proxy (`observed.http`)

LiteLLM responds to the client with HTTP 200 OK, omitting `WWW-Authenticate` and returning empty tools:

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache, no-transform

event: message
data: {"jsonrpc":"2.0","id":1,"result":{"_meta":{"litellm.ai/server_outcomes":{"test_server":{"status":"auth_required","http_status":401}}},"tools":[]}}
```

### Expected from LiteLLM proxy (`expected.http`)

On a dedicated single-server MCP endpoint, the gateway must return HTTP 401 with a `WWW-Authenticate`
header referencing the upstream authentication challenge or protected resource metadata:

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
WWW-Authenticate: Bearer error="invalid_token", resource_metadata="http://127.0.0.1:19997/.well-known/oauth-protected-resource/test_server"

{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32000,
    "message": "Upstream authentication required"
  }
}
```

## Differential controls

Across 5 of 5 runs (see `transcripts/116/wire/results.json`):

1. **Direct upstream with valid token**: 5 of 5 return HTTP 200 with 1 tool (`calculator`).
2. **Direct upstream with expired token**: 5 of 5 return HTTP 401 with `WWW-Authenticate: Bearer error="invalid_token"...`.
3. **LiteLLM with valid token**: 5 of 5 return HTTP 200 with 1 tool (`test_server-calculator`).
4. **LiteLLM with expired token**: 5 of 5 return HTTP 200 with 0 tools (`tools: []`) and omit `WWW-Authenticate`, masking the upstream 401.

## Root cause

Two interconnected code locations cause this failure:

1. **In `litellm/proxy/_experimental/mcp_server/server.py:2203` (`_check_passthrough_upstream_auth`)**:
   The preflight check for expired upstream auth iterates only over servers where `srv.is_oauth_passthrough`
   is True. In `litellm/types/mcp_server/mcp_server_manager.py:440`, `is_oauth_passthrough` requires
   `auth_type in (None, MCPAuth.none)`. For servers configured with `auth_type: "true_passthrough"`,
   `is_oauth_passthrough` evaluates to False, completely skipping the preflight upstream probe.
2. **In `litellm/proxy/_experimental/mcp_server/catalog.py:924-932` (`get_filtered_server_tools`)**:
   When `tools/list` executes, upstream raises `MCPUpstreamAuthError`. The handler catches this exception
   and explicitly swallows it:
   ```python
   except MCPUpstreamAuthError as e:
       verbose_logger.debug("MCP list_tools: omitting %s; it needs upstream auth", server.name)
       return ListToolsResult(tools=[]), classify_list_exception(e)
   ```
   The comment assumes that single-server routes surface the 401 via preflight checks, but as shown above,
   `true_passthrough` servers are never checked by preflight, and preflight only checks connect-time `initialize`
   rather than `tools/list`.

## Invariant and test

Invariant: For a single-server MCP gateway route where the upstream server requires authentication
or rejects an expired token with HTTP 401, the gateway must return HTTP 401 with a `WWW-Authenticate`
header and must not return HTTP 200 OK with an empty tool list.

Harness checker: `mcp_unauthorized_token_must_not_mask_as_empty_tools` in
`crates/harness/src/checks.rs`.
Conformance tests: `litellm_mcp_passthrough_expired_token_masked_as_empty_tools_is_violation`,
`litellm_mcp_direct_upstream_401_challenge_is_conformant`, and
`litellm_mcp_valid_token_tools_is_conformant` in `crates/harness/tests/conformance.rs`.
