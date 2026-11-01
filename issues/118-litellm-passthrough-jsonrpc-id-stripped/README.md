# 118, LiteLLM pass_through_endpoints unconditionally strips `id` from JSON-RPC request bodies, converting calls to notifications and breaking response correlation

- **Upstream**: [BerriAI/litellm](https://github.com/BerriAI/litellm). Reported in open issue
  [#49479](https://github.com/BerriAI/litellm/issues/49479) (filed 2026-11-01).
  Classified `duplicate-open`.
- **Tool under test**: LiteLLM proxy `v1.106.6` / `1.108.0-dev` at commit
  `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` (2026-10-31), with master key enabled.
- **Provider**: Deterministic local mock JSON-RPC 2.0 server on `127.0.0.1:19992/jsonrpc`
  implementing JSON-RPC 2.0 and Model Context Protocol (MCP) method handling.
- **Clients**: Standard JSON-RPC 2.0 clients, MCP clients, `httpx` 0.28.1.
- **Reproduced**: 2026-11-01, macOS arm64, Python 3.12.13. 5 of 5 runs per case across
  3 differential conditions (15 requests total).
- **Label**: `bug`

## Claim

When routing requests through `general_settings.pass_through_endpoints`, LiteLLM unconditionally
pops any top-level key matching `types_utils.all_litellm_params` from the parsed request body
before forwarding it upstream. Because `all_litellm_params` includes `id`, LiteLLM silently strips
the `id` field from every JSON-RPC 2.0 / MCP request payload. Under JSON-RPC 2.0 Specification
Section 4.1, a request lacking an `id` is a Notification for which the server MUST NOT return a
response object. As a consequence, upstream JSON-RPC servers return HTTP 204 No Content (or return
`id: null`), permanently breaking client response correlation and halting tool execution.

## What breaks

Developers and AI systems use LiteLLM's `pass_through_endpoints` feature to proxy Model Context
Protocol (MCP) servers or custom JSON-RPC microservices through a unified gateway:

1. The client issues a JSON-RPC 2.0 call (e.g. `tools/list` or `tools/call`):
   `{"jsonrpc": "2.0", "id": 100, "method": "tools/list", "params": {}}`.
2. LiteLLM routes the request through the configured pass-through endpoint.
3. In `litellm/proxy/pass_through_endpoints/pass_through_endpoints.py:643`, LiteLLM executes:
   `litellm_keys_in_body = MappingProxyType({k: _parsed_body.pop(k) for k in types_utils.all_litellm_params if k in _parsed_body})`.
4. Because `id` is one of LiteLLM's 354 internal parameters in `types_utils.all_litellm_params`,
   `_parsed_body.pop("id")` strips the request identifier from the request dictionary.
5. In line 514, `async_client.build_request(..., json=_parsed_body)` serializes the mutated body.
6. The upstream JSON-RPC 2.0 / MCP server receives a payload without an `id`:
   `{"jsonrpc": "2.0", "method": "tools/list", "params": {}}`.
7. Per JSON-RPC 2.0 Section 4.1:
   "A Notification is a Request object without an 'id' member. A Request object that is a
   Notification signifies the Client's lack of interest in the corresponding Response object,
   and as such no Response object MUST be returned to the client."
8. The server responds with `HTTP 204 No Content` (or `id: null`).
9. The client waiting for a correlation response with `id: 100` receives an empty response or
   correlation mismatch exception, and tool discovery / invocation fails immediately.

## Bug or not

Before classifying this finding as a bug, the five usefulness questions from `AGENTS.md` are evaluated:

- **Is the expected behavior really the spec?** Yes. Both the JSON-RPC 2.0 specification and
  LiteLLM's own pass-through documentation ("What Does NOT Change: Request body" in architecture.md)
  mandate that user request payloads must be forwarded verbatim without field stripping.
- **Have maintainers already ruled on it?** The issue was filed as [#49479](https://github.com/BerriAI/litellm/issues/49479).
  Furthermore, line 690 of `pass_through_endpoints.py` states that for user pass-through endpoints,
  "deliberately: its body is forwarded verbatim". Stripping `id` is an unintended side effect of
  filtering `all_litellm_params`.
- **Is the trigger supported usage?** Yes. Standard `pass_through_endpoints` configuration with
  default settings and master key authentication.
- **Is a real boundary crossed?** Yes. Wire protocol integrity: an essential protocol field (`id`)
  emitted by the client is stripped by the proxy, causing semantic protocol degradation from
  request-response to one-way notification.
- **What fix would a maintainer ship?** Restrict `all_litellm_params` stripping so that generic
  fields like `id` are not popped on user-configured pass-through routes.

Record label: `bug`.

## Wire evidence

### Sent by client to LiteLLM proxy (`client-proxy-request.json`)

```json
{
  "jsonrpc": "2.0",
  "id": 100,
  "method": "tools/list",
  "params": {}
}
```

### Observed by upstream server from LiteLLM proxy (`upstream-received-from-proxy.json`)

LiteLLM proxy stripped `id`:

```json
{
  "jsonrpc": "2.0",
  "method": "tools/list",
  "params": {}
}
```

### Observed client response from LiteLLM proxy (`client-proxy-response.txt`)

Because the request was received as a notification, the upstream returned HTTP 204 No Content:

```http
HTTP/1.1 204 No Content
date: Sun, 01 Nov 2026 07:27:44 GMT
server: uvicorn
x-litellm-call-id: 7a665127-684b-4653-b279-4e2e34c3917d
x-litellm-model-api-base: http://127.0.0.1:19992/jsonrpc
x-litellm-key-spend: 0.0
```

### Expected upstream request and response (`expected.http`)

The proxy must preserve the `id` field so the upstream returns the correlated response:

```json
{
  "jsonrpc": "2.0",
  "id": 100,
  "method": "tools/list",
  "params": {}
}
```

Upstream echoes the request `id` with HTTP 200 OK:

```json
{
  "jsonrpc": "2.0",
  "id": 100,
  "result": {
    "status": "ok",
    "tools": [
      {
        "name": "calculator",
        "description": "Performs basic arithmetic operations"
      }
    ]
  }
}
```

## Differential controls

Across 5 of 5 runs (see `transcripts/118/wire/results.json`):

1. **Direct upstream call**: 5 of 5 runs receive `id: 100` and respond with `id: 100` (HTTP 200 OK).
2. **LiteLLM proxy call**: 5 of 5 runs strip `id`, resulting in HTTP 204 No Content (the defect).
3. **Control with non-colliding key (`client_request_id`)**: 5 of 5 runs preserve `client_request_id: 100`,
   proving the drop is isolated to fields colliding with `all_litellm_params`.

## Root cause

In `litellm/proxy/pass_through_endpoints/pass_through_endpoints.py:643`:

```python
litellm_keys_in_body: Final = MappingProxyType(
    {k: _parsed_body.pop(k) for k in types_utils.all_litellm_params if k in _parsed_body}
)
```

`types_utils.all_litellm_params` includes the string `"id"`. When LiteLLM filters internal parameters
from the request body, it unconditionally mutates `_parsed_body` by popping `"id"`. In line 514,
`async_client.build_request(..., json=_parsed_body)` sends the truncated dictionary to the upstream
endpoint.

## Invariant and test

Invariant: A gateway routing a JSON-RPC 2.0 or pass-through request must forward the client's `id`
field verbatim to the upstream server to preserve JSON-RPC correlation semantics.

Harness checker: `passthrough_request_preserves_jsonrpc_id` in `crates/harness/src/checks.rs`.
Conformance tests: `litellm_passthrough_jsonrpc_id_stripped_is_violation`,
`litellm_passthrough_direct_upstream_preserves_id_is_conformant`, and
`litellm_passthrough_results_matrix_5_runs_reproduce_consistently` in `crates/harness/tests/conformance.rs`.
