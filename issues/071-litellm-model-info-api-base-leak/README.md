# 071, LiteLLM GET /model/info returns deployment `api_base` query credentials

- **Upstream**: Discussed by [litellm#18818](https://github.com/BerriAI/litellm/issues/18818), which changed `remove_sensitive_info_from_deployment`, but no dedicated `api_base` ticket was found. The docstring on `/model/info` and `/v1/model/info` (`proxy_server.py:13980`) explicitly promises to omit `api_base`: `"Provides more info about each model in /models, including config.yaml descriptions (except api key and api base)"`. In code, `api_key` is popped, but `api_base` is never popped or stripped.
- **Tool under test**: LiteLLM 1.99.0 (`tools/litellm-env`).
- **Reproduced**: 2026-09-05. Canary echo 5/5 on mock deployment (`transcripts/071/litellm-leak.yaml`). Evidence: `transcripts/071/`.

## What breaks

An administrator or operator configures a model deployment with credentials embedded in `api_base`, such as a Google AI Studio OpenAI-compatible endpoint ending in `?key=...`.

On the tested default local configuration without a master key, any network client that can reach the proxy can call `GET /model/info` or `GET /v1/model/info`. LiteLLM returns `litellm_params.api_base` in unredacted plaintext.

Authenticated non-admin access was not tested and is not claimed here.

Who that hurts:

- Default local proxies reachable by an untrusted client: the client obtains the backend provider's full `api_base` URL and its query credential. Reusing that URL can bypass proxy rate limits, budgets, and audit logging. The reproduction measures disclosure of a synthetic canary; direct provider use is an inferred consequence and does not use a live provider key.

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

1. **LiteLLM (mock deployment with query key in `api_base`)**
   - `GET /model/info` returns `litellm_params.api_base="http://127.0.0.1:9996/v1?key=CANARY_QUERY_KEY_IN_API_BASE"` in full. `api_key` is absent. 5/5. Raw HTTP: `transcripts/071/model-info.http`.
   - `GET /v1/model/info` returns the identical leak. 5/5. Raw HTTP: `transcripts/071/model-info-v1.http`.
2. **Control**
   - `GET /v1/models` returns model list with standard metadata only. No `api_base` and no canary. 5/5. Raw HTTP: `transcripts/071/models-control.http`.
   - `GET /health/liveliness` returns `"I'm alive!"` with no configuration parameters. 5/5. Raw HTTP: `transcripts/071/liveliness-control.http`.
3. **Determinism**
   - 5/5 across all runs. Compact test summary: `transcripts/071/client-results.json`.

## Upstream status

Checked 2026-09-06. The runtime reproduction is pinned to LiteLLM 1.99.0. GitHub
release `v1.99.1` is newer but was not available from the configured Python package
index, so it was not executed. Inspection of the `v1.99.1` tagged source shows that
`remove_sensitive_info_from_deployment` still does not remove `api_base`, and the
model-info docstring still promises its omission.

Searches covered `api_base`, `/model/info`, `remove_sensitive_info_from_deployment`,
issues, pull requests, releases, and the `v1.99.0...v1.99.1` diff. Issue
[litellm#18818](https://github.com/BerriAI/litellm/issues/18818) discusses the same
sanitizer for `extra_headers`, but no dedicated `api_base` ticket was found.
Classification: `discussed-no-ticket`.

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
`api_base` is never popped, so it is returned verbatim.

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

# Reviewer-safe run writes to a new temporary directory:
python3 transcripts/071/reproduce.py

# Maintainer-only fixture refresh:
python3 transcripts/071/reproduce.py --output-dir transcripts/071
```
