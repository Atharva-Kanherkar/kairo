# 138, LiteLLM Emits Zero Cost Headers for Unpriced Responses Instead of Omitting Them

- **Upstream**: Open PR [BerriAI/litellm#49467](https://github.com/BerriAI/litellm/pull/49467) (`fix(proxy): omit cost headers for unpriced responses`)
- **Tool under test**: LiteLLM `v1.109.0` (commit `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a` and upstream `main` `60fc121ce8ab995cb2997e0bbd0a980ee93c627d`)
- **Reproduced**: 2026-11-02, macOS, Python 3.12, module `litellm.proxy.common_request_processing`

## Bug or not

- **Is the expected behavior really the spec?** Yes. LiteLLM proxy documentation and PR #49467 establish that when pricing is not configured for a model deployment, response cost headers (`x-litellm-response-cost`) must be omitted rather than advertising that the request was free ($0.00). Explicitly configured free models, cache hits, and 100% discounts retain their zero-cost headers.
- **Have maintainers already ruled on it?** Yes. PR #49467 explicitly addresses this regression, noting that finalized zero amounts hide whether a model has pricing and incorrectly advertise free requests for unpriced deployments.
- **Is the trigger supported usage?** Yes. Deploying custom or newly added model endpoints through LiteLLM proxy before or without populating rates in the catalog cost map.
- **Is a real boundary crossed?** Yes. Proxy response wire header contract and billing transparency boundary. Callers inspect `x-litellm-response-cost` to record usage charges and perform tenant billing. Emitting `0.0` tells callers the request incurred zero financial charge.
- **What fix would a maintainer ship?** In `litellm/proxy/common_request_processing.py`, omit `x-litellm-response-cost` and related breakdown headers when `response_cost == 0` and `litellm_logging_obj.prepared_cost.price_known` is false.
- **Classification**: `bug`

## What breaks

1. An enterprise or multi-tenant system routes completions through LiteLLM proxy to an unpriced model deployment.
2. LiteLLM calculates internal completion cost as 0.0 because no catalog entry or custom rates exist.
3. In `cost_headers`, LiteLLM synthesizes `x-litellm-response-cost: 0.0` and attaches it to the HTTP response.
4. Downstream accounting pipelines, client SDKs, and billing dashboards parse `x-litellm-response-cost: 0.0` and log the request as free ($0.00).
5. When provider bills arrive, allocated spend fails to reconcile against actual expenses, and automated budget alarms fail to trigger.

## Wire evidence

- `bug-unpriced-response-cost-header-present.json`: Outbound response headers on unpriced model showing `price_known: false` and `response_cost_header: "0.0"`.
- `control-priced-model-response-cost-header.json`: Outbound response headers on priced model showing `price_known: true` and `response_cost_header: "0.015"`.
- `control-free-model-response-cost-header.json`: Outbound response headers on explicitly free model showing `price_known: true` and `response_cost_header: "0.0"`.
- `control-fixed-unpriced-response-cost-header-omitted.json`: Outbound response headers under PR #49467 showing `price_known: false` and `response_cost_header: null` (header omitted).
- `results.json`: 5 of 5 runs reproducing 100% deterministically across all conditions.

## Root cause (if found)

In `litellm/proxy/common_request_processing.py`:
```python
def cost_headers(
    response_cost: float | str | None, litellm_logging_obj: LiteLLMLoggingObj | None
) -> Mapping[str, str | None]:
    cost_breakdown: Final = _get_cost_breakdown_from_logging_obj(
        litellm_logging_obj=litellm_logging_obj, response_cost=response_cost
    )
    return {
        "x-litellm-response-cost": str(response_cost),
        ...
    }
```
`cost_headers` blindly formatted `response_cost` into `x-litellm-response-cost` even when `response_cost == 0` resulted from unconfigured/unknown rates rather than an actual free tier or discount.

The fix checks `prepared_cost.price_known` and returns an empty mapping `{}` when the zero amount stems from unknown pricing.

## Test

- Invariant checker: `unpriced_response_cost_header_omitted` in `crates/harness/src/checks.rs`.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  * `litellm_unpriced_response_cost_header_present_reproduction`
  * `litellm_unpriced_fixed_response_cost_header_omitted_control`
  * `litellm_priced_model_response_cost_header_control`
  * `litellm_free_model_response_cost_header_control`
  * `litellm_unpriced_response_cost_matrix_5_runs_reproduce_consistently`
