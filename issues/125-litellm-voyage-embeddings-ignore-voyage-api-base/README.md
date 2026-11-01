# 125, LiteLLM voyage/ Embeddings Ignore VOYAGE_API_BASE Environment Variable

- **Upstream**: [BerriAI/litellm#49411](https://github.com/BerriAI/litellm/issues/49411), open as of 2026-11-01; PR [BerriAI/litellm#49415](https://github.com/BerriAI/litellm/pull/49415)
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-01, macOS, Python 3.12, module `litellm.llms.voyage`

## Bug or not

- **Is the expected behavior really the spec?** Yes. LiteLLM establishes `<PROVIDER>_API_BASE` as the standard mechanism to route provider requests to custom, self-hosted, or proxy endpoints (e.g. `JINA_AI_API_BASE`, `HOSTED_VLLM_API_BASE`, `VERCEL_AI_GATEWAY_API_BASE`). Moreover, LiteLLM's own Voyage rerank implementation in `litellm/rerank_api/main.py:475` explicitly reads `get_secret_str("VOYAGE_API_BASE")`.
- **Have maintainers already ruled on it?** Yes. Maintainers and contributors acknowledged this in open PR #49415 (`fix(voyage): honor VOYAGE_API_BASE for voyage embedding calls`), confirming that all Voyage embedding configurations must honor `VOYAGE_API_BASE`.
- **Is the trigger supported usage?** Yes. Configuring provider credentials and endpoints via environment variables (`VOYAGE_API_BASE`, `VOYAGE_API_KEY`) is standard, documented practice in LiteLLM and across SDK wrappers (such as `cocoindex`).
- **Is a real boundary crossed?** Yes. Data privacy and boundary breach. Callers expecting requests to stay within an internal gateway (`http://127.0.0.1:9988/v1` or `http://gateway.corp/v1`) have their embedding payloads sent across the public internet to third-party servers (`api.voyageai.com`).
- **What fix would a maintainer ship?** Read `VOYAGE_API_BASE` via `get_secret_str("VOYAGE_API_BASE")` in `get_voyage_api_base` and use it as the fallback base URL in `VoyageEmbeddingConfig`, `VoyageContextualEmbeddingConfig`, and `VoyageMultimodalEmbeddingConfig`.
- **Classification**: `bug`

## What breaks

When an application calls `litellm.embedding(model="voyage/voyage-3.5", input=[...])` with `VOYAGE_API_BASE` configured in the environment:
1. `VoyageEmbeddingConfig.get_complete_url()` computes the endpoint as `ensure_api_route(api_base or get_default_base_url(api_key), "embeddings")`.
2. Because `api_base` was not passed directly as a Python argument, it evaluates `get_default_base_url(api_key)`.
3. In `litellm/llms/voyage/common_utils.py`, `get_default_base_url()` only routes keys starting with `al-` to `ai.mongodb.com`, otherwise returning the hardcoded constant `VOYAGE_API_BASE = "https://api.voyageai.com/v1"`. It never inspects `os.environ["VOYAGE_API_BASE"]` or `get_secret_str("VOYAGE_API_BASE")`.
4. As a result, the request completely bypasses the configured internal gateway or proxy:
   - 0 requests reach the internal gateway.
   - The request is dispatched over the public internet to `https://api.voyageai.com/v1/embeddings`.
   - Callers using internal enterprise tokens fail with `401 AuthenticationError: {"detail":"Provided API key is invalid."}`.
   - For valid external keys, private internal documents are sent to a public third-party service without the operator's knowledge.

## Wire evidence

- `bug-outbound-bypasses-gateway.json`: Captured evidence demonstrating 0 requests reaching the local gateway, standard/contextual/multimodal URLs resolving to public Voyage hosts, and an authentication failure from public Voyage.
- `control-explicit-api-base-reaches-gateway.json`: Captured request reaching the local gateway at `/v1/embeddings` when `api_base` is passed explicitly as a kwarg.
- `control-resolver-routes-all-configs.json`: Verification showing that reading `VOYAGE_API_BASE` correctly routes all three embedding configurations to the gateway.

## Root cause (if found)

In `litellm/llms/voyage/common_utils.py`, lines 30-35:
```python
def get_default_base_url(api_key: str | None = None) -> str:
    resolved: Final = get_voyage_api_key(api_key)
    if resolved is not None and resolved.startswith(MONGODB_API_KEY_PREFIX):
        return MONGODB_API_BASE
    return VOYAGE_API_BASE
```
`get_default_base_url` returns the constant `VOYAGE_API_BASE` rather than reading the environment variable `VOYAGE_API_BASE`.

## Test

- Invariant checker: `voyage_embeddings_honor_environment_api_base` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_voyage_embeddings_ignore_env_api_base_wire`
  * `litellm_voyage_embeddings_explicit_api_base_control_wire`
  * `litellm_voyage_embeddings_resolver_routes_all_configs_control_wire`
  * `litellm_voyage_embeddings_results_matrix_5_runs_reproduce_consistently`
