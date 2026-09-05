# 071, LiteLLM GET /model/info returns deployment `api_base` credentials and unmasked headers

- **Upstream**: [litellm#18818](https://github.com/BerriAI/litellm/issues/18818) (partial fix in `remove_sensitive_info_from_deployment`) and [litellm#36898](https://github.com/BerriAI/litellm/issues/36898) (`/health` extra_headers leak). The docstring on `/model/info` and `/v1/model/info` (`proxy_server.py:13980`) explicitly promises to omit `api_base`: `"Provides more info about each model in /models, including config.yaml descriptions (except api key and api base)"`. In code, `api_key` is popped, but `api_base` is never popped or stripped, and `SensitiveDataMasker` does not treat `api_base` as sensitive.
- **Tool under test**: LiteLLM 1.99.0 (`tools/litellm-env`).
- **Reproduced**: 2026-09-05. Canary echo 5/5 on mock deployment (`transcripts/071/litellm-leak.yaml`). Evidence: `transcripts/071/`.

## What breaks

An administrator or operator configures a model deployment with credentials embedded in `api_base` (such as Google AI Studio OpenAI-compatible endpoints `https://generativelanguage.googleapis.com/v1beta/openai/?key=...`, Azure endpoints, internal API gateway tokens, or HTTP basic authentication `https://user:pass@host`), or custom internal routing headers.

Any tenant with access to the proxy can call `GET /model/info` or `GET /v1/model/info`. LiteLLM returns the full `litellm_params` dictionary including `api_base` in unredacted plaintext.

Default local proxy installations without a master key allow unauthenticated calls through `user_api_key_auth`. When a master key or virtual key database is configured, `/model/info` is classified under `RouteChecks.is_info_route`, which permits non-admin callers to query model information. Unlike `/health` (which calls `_strip_admin_only_fields_from_health_result` to remove `api_base` and `api_version` for non-admins), `/model/info` has no such check.

Who that hurts:

- Multi-tenant shared proxies: a low-privilege tenant or client who queries `/model/info` obtains the backend provider's full `api_base` URL, extracting raw API keys or gateway tokens. The tenant can then call the upstream provider directly, bypassing proxy rate limits, budgets, audit logging, and data governance policies.
- Cloud deployments: services that authenticate via query parameters or basic authentication embedded in endpoint URLs are exposed in plaintext.
- Custom headers: headers in `extra_headers` that do not match the keywords in `SensitiveDataMasker` (`authorization`, `token`, `key`, `secret`, `vertex_credentials`, `credentials`, `password`, `passwd`) are returned in full plaintext without masking.

`/v1/models` and `/health/liveliness` do not return `litellm_params` or deployment endpoints. Those serve as the controls.

```mermaid
flowchart LR
  config["config.yaml api_base with ?key=CANARY_KEY"] --> proxy["LiteLLM 1.99.0"]
  caller["caller GET /model/info"] --> proxy
  proxy -->|"strips api_key"| drop["api_key popped"]
  proxy -->|"returns api_base in full plaintext"| caller
  caller -->|"extracts ?key=CANARY_KEY"| leak["direct provider access"]
```

## Wire evidence

1. **LiteLLM (mock deployment with query key in `api_base` and custom header)**
   - `GET /model/info` returns `litellm_params.api_base="http://127.0.0.1:9996/v1?key=CANARY_QUERY_KEY_IN_API_BASE"` in full, and `x-custom-tenant-id="CANARY_UNMASKED_CUSTOM_HEADER"` in full. `api_key` is absent. 5/5. `transcripts/071/model-info.json`.
   - `GET /v1/model/info` returns the identical leak. 5/5. `transcripts/071/model-info-v1.json`.
2. **Control**
   - `GET /v1/models` returns model list with standard metadata only. No `api_base`, no headers, no canaries. 5/5. `transcripts/071/models-control.json`.
   - `GET /health/liveliness` returns `"I'm alive!"` with no configuration parameters. 5/5.
3. **Determinism**
   - 5/5 across all runs. Compact test summary: `transcripts/071/client-results.json`.

## Root cause (in LiteLLM source)

In `proxy_server.py:13980`:
```python
async def model_info_v1(
...
):
    """
    Provides more info about each model in /models, including config.yaml descriptions (except api key and api base)
    ...
```
The docstring explicitly specifies that `api_base` should be excluded alongside `api_key`.

However, the sanitization logic in `litellm/proxy/common_utils/openai_endpoint_utils.py` is:
```python
def remove_sensitive_info_from_deployment(
    deployment_dict: dict,
    excluded_keys: set[str] | None = None,
) -> dict:
    deployment_dict["litellm_params"].pop("api_key", None)
    deployment_dict["litellm_params"].pop("client_secret", None)
    deployment_dict["litellm_params"].pop("vertex_credentials", None)
    deployment_dict["litellm_params"].pop("vertex_ai_credentials", None)
    deployment_dict["litellm_params"].pop("aws_access_key_id", None)
    deployment_dict["litellm_params"].pop("aws_secret_access_key", None)

    deployment_dict["litellm_params"] = SENSITIVE_DATA_MASKER.mask_dict(
        deployment_dict["litellm_params"], excluded_keys=_excluded
    )

    return deployment_dict
```
`api_base` is never popped. Furthermore, in `SensitiveDataMasker.is_sensitive_key("api_base")`, the segments `["api", "base"]` do not match any pattern in `sensitive_patterns`, so `api_base` is returned verbatim.

In contrast, `_strip_admin_only_fields_from_health_result` in `proxy/health_endpoints/_health_endpoints.py` specifically removes `api_base` and `api_version` for non-admin callers on `/health`.

## Test invariants

1. A client-visible `GET /model/info` or `GET /v1/model/info` body MUST NOT contain query keys or authentication tokens in `litellm_params.api_base`.
2. `/v1/models` and `/health/liveliness` MUST stay clean of deployment parameters.

## Repro

```bash
# Start LiteLLM with reproduction configuration:
tools/litellm-env/bin/litellm --config transcripts/071/litellm-leak.yaml --port 4010

# In another terminal:
curl -s http://127.0.0.1:4010/model/info | jq '.data[0].litellm_params.api_base'
# Returns "http://127.0.0.1:9996/v1?key=CANARY_QUERY_KEY_IN_API_BASE"

# Automated runner:
python3 transcripts/071/reproduce.py
```
