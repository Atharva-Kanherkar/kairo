#!/usr/bin/env python3
"""Reproduction rig for Finding 138: LiteLLM emits x-litellm-response-cost: 0.0

for unpriced deployments instead of omitting the cost header, falsely claiming
the request was free.

Target: LiteLLM v1.109.0 (commit cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a / main 60fc121ce8a)
Upstream PR: https://github.com/BerriAI/litellm/pull/49467
"""

import datetime
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import litellm
from litellm.litellm_core_utils.litellm_logging import Logging
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.common_request_processing import ProxyBaseLLMRequestProcessing, cost_headers


@dataclass
class MockPreparedCost:
    amount: float
    price_known: bool


def fixed_cost_headers(
    response_cost: float | str | None, litellm_logging_obj: Optional[Logging]
) -> Mapping[str, Optional[str]]:
    """Fixed cost_headers implementation from PR #49467:

    Omits cost headers when response_cost is 0 and price_known is False.
    """
    if (
        response_cost == 0
        and litellm_logging_obj is not None
        and getattr(litellm_logging_obj, "prepared_cost", None) is not None
        and not litellm_logging_obj.prepared_cost.price_known
    ):
        return {}
    return cost_headers(response_cost, litellm_logging_obj)


def run_defect_unpriced_cost_header() -> Dict[str, Any]:
    """Defect: Unpatched LiteLLM emits x-litellm-response-cost: 0.0 on unpriced model."""
    now = datetime.datetime.now(datetime.timezone.utc)
    model = "custom-unpriced-model"

    logging = Logging(
        model=model,
        messages=[],
        stream=False,
        call_type="acompletion",
        start_time=now,
        litellm_call_id="defect-unpriced-call",
        function_id="defect-unpriced-call",
    )
    logging.update_environment_variables(
        model=model, custom_llm_provider="openai", litellm_params={}, optional_params={}
    )
    logging.prepared_cost = MockPreparedCost(amount=0.0, price_known=False)

    headers = cost_headers(0.0, logging)
    cost_val = headers.get("x-litellm-response-cost")

    return {
        "model": model,
        "price_known": False,
        "calculated_cost": 0.0,
        "response_cost_header": cost_val,
        "cost_header_present": cost_val is not None,
        "headers": {
            k: v for k, v in headers.items() if v is not None and "cost" in k
        },
    }


def run_control_priced_cost_header() -> Dict[str, Any]:
    """Control 1: Priced model emits positive x-litellm-response-cost."""
    now = datetime.datetime.now(datetime.timezone.utc)
    model = "custom-priced-model"

    logging = Logging(
        model=model,
        messages=[],
        stream=False,
        call_type="acompletion",
        start_time=now,
        litellm_call_id="control-priced-call",
        function_id="control-priced-call",
    )
    logging.update_environment_variables(
        model=model, custom_llm_provider="openai", litellm_params={}, optional_params={}
    )
    logging.prepared_cost = MockPreparedCost(amount=0.015, price_known=True)

    headers = cost_headers(0.015, logging)
    cost_val = headers.get("x-litellm-response-cost")

    return {
        "model": model,
        "price_known": True,
        "calculated_cost": 0.015,
        "response_cost_header": cost_val,
        "cost_header_present": cost_val is not None,
        "headers": {
            k: v for k, v in headers.items() if v is not None and "cost" in k
        },
    }


def run_control_free_cost_header() -> Dict[str, Any]:
    """Control 2: Explicitly free model legitimately emits x-litellm-response-cost: 0.0."""
    now = datetime.datetime.now(datetime.timezone.utc)
    model = "custom-free-model"

    logging = Logging(
        model=model,
        messages=[],
        stream=False,
        call_type="acompletion",
        start_time=now,
        litellm_call_id="control-free-call",
        function_id="control-free-call",
    )
    logging.update_environment_variables(
        model=model, custom_llm_provider="openai", litellm_params={}, optional_params={}
    )
    logging.prepared_cost = MockPreparedCost(amount=0.0, price_known=True)

    headers = cost_headers(0.0, logging)
    cost_val = headers.get("x-litellm-response-cost")

    return {
        "model": model,
        "price_known": True,
        "calculated_cost": 0.0,
        "response_cost_header": cost_val,
        "cost_header_present": cost_val is not None,
        "headers": {
            k: v for k, v in headers.items() if v is not None and "cost" in k
        },
    }


def run_control_fixed_unpriced_cost_header() -> Dict[str, Any]:
    """Control 3: Fixed cost_headers under PR #49467 omits cost header for unpriced model."""
    now = datetime.datetime.now(datetime.timezone.utc)
    model = "custom-unpriced-model"

    logging = Logging(
        model=model,
        messages=[],
        stream=False,
        call_type="acompletion",
        start_time=now,
        litellm_call_id="control-fixed-unpriced-call",
        function_id="control-fixed-unpriced-call",
    )
    logging.update_environment_variables(
        model=model, custom_llm_provider="openai", litellm_params={}, optional_params={}
    )
    logging.prepared_cost = MockPreparedCost(amount=0.0, price_known=False)

    headers = fixed_cost_headers(0.0, logging)
    cost_val = headers.get("x-litellm-response-cost")

    return {
        "model": model,
        "price_known": False,
        "calculated_cost": 0.0,
        "response_cost_header": cost_val,
        "cost_header_present": cost_val is not None,
        "headers": {
            k: v for k, v in headers.items() if v is not None and "cost" in k
        },
    }


def main() -> None:
    iterations = 5
    run_records: List[Dict[str, Any]] = []
    wire_dir = Path(__file__).resolve().parents[1] / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    for i in range(iterations):
        defect = run_defect_unpriced_cost_header()
        ctrl_priced = run_control_priced_cost_header()
        ctrl_free = run_control_free_cost_header()
        ctrl_fixed = run_control_fixed_unpriced_cost_header()

        # Defect verification: unpriced model emits 0.0 cost header
        assert defect["price_known"] is False
        assert defect["cost_header_present"] is True
        assert defect["response_cost_header"] == "0.0"

        # Control 1: priced model emits positive cost header
        assert ctrl_priced["price_known"] is True
        assert ctrl_priced["cost_header_present"] is True
        assert ctrl_priced["response_cost_header"] == "0.015"

        # Control 2: explicitly free model emits 0.0 cost header
        assert ctrl_free["price_known"] is True
        assert ctrl_free["cost_header_present"] is True
        assert ctrl_free["response_cost_header"] == "0.0"

        # Control 3: fixed logic omits cost header for unpriced model
        assert ctrl_fixed["price_known"] is False
        assert ctrl_fixed["cost_header_present"] is False
        assert ctrl_fixed["response_cost_header"] is None
        assert len(ctrl_fixed["headers"]) == 0

        run_records.append({
            "run": i + 1,
            "defect_unpriced_emits_zero_header": True,
            "control_priced_emits_positive_header": True,
            "control_free_emits_zero_header": True,
            "control_fixed_unpriced_omits_header": True,
        })

    defect_sample = run_defect_unpriced_cost_header()
    ctrl_priced_sample = run_control_priced_cost_header()
    ctrl_free_sample = run_control_free_cost_header()
    ctrl_fixed_sample = run_control_fixed_unpriced_cost_header()

    (wire_dir / "bug-unpriced-response-cost-header-present.json").write_text(
        json.dumps(defect_sample, indent=2) + "\n"
    )
    (wire_dir / "control-priced-model-response-cost-header.json").write_text(
        json.dumps(ctrl_priced_sample, indent=2) + "\n"
    )
    (wire_dir / "control-free-model-response-cost-header.json").write_text(
        json.dumps(ctrl_free_sample, indent=2) + "\n"
    )
    (wire_dir / "control-fixed-unpriced-response-cost-header-omitted.json").write_text(
        json.dumps(ctrl_fixed_sample, indent=2) + "\n"
    )

    results = {
        "iterations": iterations,
        "success_rate": f"{iterations}/{iterations}",
        "all_passed": True,
        "runs": run_records,
    }
    (wire_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (Path(__file__).resolve().parents[1] / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"Reproduced 138: {iterations}/{iterations} runs passed successfully.")


if __name__ == "__main__":
    main()
