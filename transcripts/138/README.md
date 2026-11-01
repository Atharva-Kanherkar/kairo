# Transcript 138: LiteLLM Emits Zero Cost Headers for Unpriced Responses

## Overview

This transcript records wire evidence reproducing the defect where LiteLLM proxy returns `x-litellm-response-cost: 0.0` for unpriced deployments instead of omitting the cost header (open PR [BerriAI/litellm#49467](https://github.com/BerriAI/litellm/pull/49467)).

When an inference call is made to a model deployment that lacks configured catalog rates:
1. LiteLLM evaluates the request cost as 0.0.
2. In `litellm/proxy/common_request_processing.py`, `cost_headers(0.0, logging)` constructs HTTP response headers including `x-litellm-response-cost: 0.0`.
3. Downstream API callers, billing scrapers, and tenant dashboards misinterpret `0.0` as confirmation that the request was officially free.
4. Under PR #49467, unpriced deployments preserve `price_known = False` on `PreparedCost`, allowing `cost_headers` to omit `x-litellm-response-cost` entirely while preserving zero headers for explicitly free models, cache hits, and full discounts.

## Environment

- Target: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- Target Upstream PR: [BerriAI/litellm#49467](https://github.com/BerriAI/litellm/pull/49467)
- Python Environment: Python 3.12 (`/Users/atharva/.cache/kr-hunt-1027/venv`)
- Execution Date: 2026-11-02

## Differential Control Ladder

1. **Failing Case (Unpatched Unpriced Deployment)**:
   - Input: Unpriced model deployment where pricing rates are unconfigured (`price_known: false`).
   - Result: Emits `x-litellm-response-cost: 0.0`, claiming the request was free.
2. **Control 1 (Priced Deployment)**:
   - Input: Priced model deployment with positive token rates (`price_known: true`, calculated cost `0.015`).
   - Result: Emits `x-litellm-response-cost: 0.015`.
3. **Control 2 (Explicitly Free Deployment)**:
   - Input: Model configured with explicit zero rates (`price_known: true`, calculated cost `0.0`).
   - Result: Emits `x-litellm-response-cost: 0.0`.
4. **Control 3 (Fixed Unpriced Deployment under PR #49467)**:
   - Input: Unpriced model deployment with `price_known: false`.
   - Result: Omits `x-litellm-response-cost` from response headers (`response_cost_header: null`).

## Artifacts in `wire/`

- `bug-unpriced-response-cost-header-present.json`: Captured response showing `price_known: false` and `response_cost_header: "0.0"`.
- `control-priced-model-response-cost-header.json`: Captured response showing `price_known: true` and `response_cost_header: "0.015"`.
- `control-free-model-response-cost-header.json`: Captured response showing `price_known: true` and `response_cost_header: "0.0"`.
- `control-fixed-unpriced-response-cost-header-omitted.json`: Captured response showing `price_known: false` and `response_cost_header: null`.
- `results.json`: 5 of 5 runs producing identical results across all conditions.
