# Transcript 125: LiteLLM voyage/ Embeddings Ignore VOYAGE_API_BASE Environment Variable

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM's `voyage/` embedding transformation completely ignores the `VOYAGE_API_BASE` environment variable, routing requests directly to the public Voyage cloud host (`api.voyageai.com`) instead of the configured internal gateway (Upstream issue [#49411](https://github.com/BerriAI/litellm/issues/49411), open PR [#49415](https://github.com/BerriAI/litellm/pull/49415)).

In `litellm/llms/voyage/embedding/transformation.py`, `transformation_contextual.py`, and `transformation_multimodal.py`, `get_complete_url()` computes destination URLs via:
`ensure_api_route(api_base or get_default_base_url(api_key), "embeddings")`
`get_default_base_url()` only checks whether the key prefix matches MongoDB (`al-`), otherwise returning the hardcoded public constant `VOYAGE_API_BASE = "https://api.voyageai.com/v1"`. It never consults `os.environ.get("VOYAGE_API_BASE")` or `get_secret_str("VOYAGE_API_BASE")`.

In contrast, the Voyage rerank path in `litellm/rerank_api/main.py:475` reads `get_secret_str("VOYAGE_API_BASE")`, and other providers (`jina_ai`, `hosted_vllm`, `vercel`) all inspect their respective `<PROVIDER>_API_BASE` environment variables.

Consequently:
1. Enterprise users and SDK callers (such as `cocoindex`) configuring `VOYAGE_API_BASE="http://internal-gateway/v1"` have their requests bypass the internal gateway entirely.
2. 0 requests reach the local gateway, and requests with internal tokens fail with `401 AuthenticationError` from the public host or leak sensitive document text outside the corporate network.

## Reproduction Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream Issue: [BerriAI/litellm#49411](https://github.com/BerriAI/litellm/issues/49411)
- Upstream PR: [BerriAI/litellm#49415](https://github.com/BerriAI/litellm/pull/49415)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-01

## Differential Control Ladder

1. **Failing Case (Env var set without explicit kwarg)**:
   - Configure `VOYAGE_API_BASE="http://127.0.0.1:9988/v1"` and invoke `litellm.embedding(model="voyage/voyage-3.5", input=["..."])`.
   - Result: 0 requests reach the local mock gateway. The request is dispatched to `https://api.voyageai.com/v1/embeddings`, throwing `AuthenticationError: {"detail":"Provided API key is invalid."}`.
2. **Control 1 (Explicit api_base on request)**:
   - Invoke `litellm.embedding(model="voyage/voyage-3.5", input=["..."], api_base="http://127.0.0.1:9988/v1")`.
   - Result: 1 of 1 requests reach the mock gateway at `/v1/embeddings`, returning valid embedding response vectors.
3. **Control 2 (Resolved URLs when VOYAGE_API_BASE is read)**:
   - When using `get_voyage_api_base` (from PR #49415) which checks `api_base or get_secret_str("VOYAGE_API_BASE") or get_default_base_url(api_key)`, all 3 embedding configurations (`embeddings`, `contextualizedembeddings`, `multimodalembeddings`) correctly resolve to `http://127.0.0.1:9988/v1/...`.

## Artifacts in `wire/`

- `bug-outbound-bypasses-gateway.json`: Captured evidence showing 0 gateway requests received and URLs resolving to public Voyage hosts despite `VOYAGE_API_BASE` being set.
- `control-explicit-api-base-reaches-gateway.json`: Captured request and response when `api_base` is explicitly passed on the call.
- `control-resolver-routes-all-configs.json`: Verification that reading `VOYAGE_API_BASE` routes standard, contextual, and multimodal embedding URLs to the gateway.
- `results.json`: 5 of 5 runs producing identical results across all 4 conditions.
