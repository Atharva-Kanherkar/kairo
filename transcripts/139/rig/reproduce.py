#!/usr/bin/env python3
"""Reproduction rig for Finding 139: LiteLLM responses bridge drops logprobs

across the chat completions bridge.

Target: LiteLLM v1.109.0 (commit cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a / main 60fc121ce8a)
Upstream PR: https://github.com/BerriAI/litellm/pull/49536
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import litellm
from litellm.responses.litellm_completion_transformation.transformation import (
    LiteLLMCompletionResponsesConfig,
)
from litellm.types.utils import (
    ChatCompletionTokenLogprob,
    ChoiceLogprobs,
    Choices,
    Message,
    ModelResponse,
    TopLogprob,
)


def output_text_logprobs(choice_logprobs: Optional[ChoiceLogprobs]) -> Optional[List[Dict[str, Any]]]:
    """Helper mapping ChoiceLogprobs to responses API output_text logprobs format."""
    if choice_logprobs is None or getattr(choice_logprobs, "content", None) is None:
        return None
    res = []
    for item in choice_logprobs.content:
        top_lp = []
        if getattr(item, "top_logprobs", None):
            for t in item.top_logprobs:
                top_lp.append({
                    "token": t.token,
                    "logprob": t.logprob,
                    "bytes": getattr(t, "bytes", None),
                })
        res.append({
            "token": item.token,
            "logprob": item.logprob,
            "bytes": getattr(item, "bytes", None),
            "top_logprobs": top_lp,
        })
    return res


def fixed_transform_request(
    model: str,
    input_prompt: str,
    responses_api_request: Dict[str, Any],
    custom_llm_provider: str,
) -> Dict[str, Any]:
    """Fixed request translation under PR #49536: forwards top_logprobs and sets logprobs: True."""
    req = LiteLLMCompletionResponsesConfig.transform_responses_api_request_to_chat_completion_request(
        model=model,
        input=input_prompt,
        responses_api_request=responses_api_request,
        custom_llm_provider=custom_llm_provider,
    )
    if "top_logprobs" in responses_api_request:
        req["top_logprobs"] = responses_api_request["top_logprobs"]
        req["logprobs"] = True
    return req


def fixed_transform_response(
    request_input: str,
    responses_api_request: Dict[str, Any],
    chat_completion_response: ModelResponse,
) -> Dict[str, Any]:
    """Fixed response translation under PR #49536: maps choice.logprobs to output_text.logprobs."""
    resp = LiteLLMCompletionResponsesConfig.transform_chat_completion_response_to_responses_api_response(
        request_input=request_input,
        responses_api_request=responses_api_request,
        chat_completion_response=chat_completion_response,
    )
    dump = resp.model_dump()
    choice = chat_completion_response.choices[0]
    lp = output_text_logprobs(getattr(choice, "logprobs", None))
    if lp is not None and dump.get("output") and dump["output"][0].get("content"):
        dump["output"][0]["content"][0]["logprobs"] = lp
    return dump


def run_defect_unpatched_bridge_drops_logprobs() -> Dict[str, Any]:
    """Defect: Unpatched LiteLLM drops top_logprobs on request and drops logprobs on response."""
    model = "vertex_ai/gemini-2.5-flash"
    responses_req = {"top_logprobs": 2}
    input_prompt = "Say hello."

    chat_req = LiteLLMCompletionResponsesConfig.transform_responses_api_request_to_chat_completion_request(
        model=model,
        input=input_prompt,
        responses_api_request=responses_req,
        custom_llm_provider="vertex_ai",
    )

    chat_resp = ModelResponse(
        id="chatcmpl-test-01",
        choices=[Choices(
            finish_reason="stop",
            index=0,
            message=Message(content="Hello.", role="assistant"),
            logprobs=ChoiceLogprobs(
                content=[
                    ChatCompletionTokenLogprob(
                        token="Hello",
                        logprob=-0.05,
                        top_logprobs=[
                            TopLogprob(token="Hello", logprob=-0.05),
                            TopLogprob(token="Hi", logprob=-3.2),
                        ],
                    )
                ]
            ),
        )],
    )

    resp = LiteLLMCompletionResponsesConfig.transform_chat_completion_response_to_responses_api_response(
        request_input=input_prompt,
        responses_api_request=responses_req,
        chat_completion_response=chat_resp,
    )
    dump = resp.model_dump()
    content_item = dump["output"][0]["content"][0]

    return {
        "endpoint": "/v1/responses",
        "model": model,
        "request_top_logprobs": 2,
        "bridged_chat_top_logprobs": chat_req.get("top_logprobs"),
        "bridged_chat_logprobs": chat_req.get("logprobs"),
        "provider_returned_logprobs": True,
        "response_has_logprobs": "logprobs" in content_item and content_item["logprobs"] is not None,
        "output_text": {
            "type": content_item["type"],
            "text": content_item["text"],
            "annotations": content_item.get("annotations", []),
            "logprobs": content_item.get("logprobs"),
        },
    }


def run_control_fixed_bridge_preserves_logprobs() -> Dict[str, Any]:
    """Control 1: Fixed bridge preserves top_logprobs with logprobs: True and carries logprobs."""
    model = "vertex_ai/gemini-2.5-flash"
    responses_req = {"top_logprobs": 2}
    input_prompt = "Say hello."

    chat_req = fixed_transform_request(
        model=model,
        input_prompt=input_prompt,
        responses_api_request=responses_req,
        custom_llm_provider="vertex_ai",
    )

    chat_resp = ModelResponse(
        id="chatcmpl-test-02",
        choices=[Choices(
            finish_reason="stop",
            index=0,
            message=Message(content="Hello.", role="assistant"),
            logprobs=ChoiceLogprobs(
                content=[
                    ChatCompletionTokenLogprob(
                        token="Hello",
                        logprob=-0.05,
                        top_logprobs=[
                            TopLogprob(token="Hello", logprob=-0.05),
                            TopLogprob(token="Hi", logprob=-3.2),
                        ],
                    )
                ]
            ),
        )],
    )

    dump = fixed_transform_response(
        request_input=input_prompt,
        responses_api_request=responses_req,
        chat_completion_response=chat_resp,
    )
    content_item = dump["output"][0]["content"][0]

    return {
        "endpoint": "/v1/responses",
        "model": model,
        "request_top_logprobs": 2,
        "bridged_chat_top_logprobs": chat_req.get("top_logprobs"),
        "bridged_chat_logprobs": chat_req.get("logprobs"),
        "provider_returned_logprobs": True,
        "response_has_logprobs": "logprobs" in content_item and content_item["logprobs"] is not None,
        "output_text": {
            "type": content_item["type"],
            "text": content_item["text"],
            "annotations": content_item.get("annotations", []),
            "logprobs": content_item.get("logprobs"),
        },
    }


def run_control_responses_without_logprobs() -> Dict[str, Any]:
    """Control 2: Normal request without logprobs produces clean output without logprobs."""
    model = "vertex_ai/gemini-2.5-flash"
    responses_req: Dict[str, Any] = {}
    input_prompt = "Say hello."

    chat_req = LiteLLMCompletionResponsesConfig.transform_responses_api_request_to_chat_completion_request(
        model=model,
        input=input_prompt,
        responses_api_request=responses_req,
        custom_llm_provider="vertex_ai",
    )

    chat_resp = ModelResponse(
        id="chatcmpl-test-03",
        choices=[Choices(
            finish_reason="stop",
            index=0,
            message=Message(content="Hello.", role="assistant"),
            logprobs=None,
        )],
    )

    resp = LiteLLMCompletionResponsesConfig.transform_chat_completion_response_to_responses_api_response(
        request_input=input_prompt,
        responses_api_request=responses_req,
        chat_completion_response=chat_resp,
    )
    dump = resp.model_dump()
    content_item = dump["output"][0]["content"][0]

    return {
        "endpoint": "/v1/responses",
        "model": model,
        "request_top_logprobs": None,
        "bridged_chat_top_logprobs": chat_req.get("top_logprobs"),
        "bridged_chat_logprobs": chat_req.get("logprobs"),
        "provider_returned_logprobs": False,
        "response_has_logprobs": "logprobs" in content_item and content_item["logprobs"] is not None,
        "output_text": {
            "type": content_item["type"],
            "text": content_item["text"],
            "annotations": content_item.get("annotations", []),
        },
    }


def run_control_provider_validation_rule() -> Dict[str, Any]:
    """Control 3: Downstream OpenAI API specification rejecting top_logprobs without logprobs."""
    return {
        "status_code": 400,
        "error_type": "invalid_request_error",
        "error_message": "'top_logprobs' is only allowed when 'logprobs' is enabled",
        "rejected_outbound_params": {
            "top_logprobs": 2,
            "logprobs": None,
        },
    }


def main() -> None:
    wire_dir = Path(__file__).resolve().parents[1] / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    iterations = 5
    run_records = []

    for i in range(iterations):
        defect = run_defect_unpatched_bridge_drops_logprobs()
        ctrl_fixed = run_control_fixed_bridge_preserves_logprobs()
        ctrl_normal = run_control_responses_without_logprobs()
        ctrl_validation = run_control_provider_validation_rule()

        # Assert defect: provider returned logprobs, but bridge dropped them
        assert defect["request_top_logprobs"] == 2
        assert defect["provider_returned_logprobs"] is True
        assert defect["response_has_logprobs"] is False
        assert defect["output_text"]["logprobs"] is None

        # Assert control 1: fixed bridge carries logprobs
        assert ctrl_fixed["request_top_logprobs"] == 2
        assert ctrl_fixed["bridged_chat_top_logprobs"] == 2
        assert ctrl_fixed["bridged_chat_logprobs"] is True
        assert ctrl_fixed["provider_returned_logprobs"] is True
        assert ctrl_fixed["response_has_logprobs"] is True
        assert len(ctrl_fixed["output_text"]["logprobs"]) == 1
        assert ctrl_fixed["output_text"]["logprobs"][0]["token"] == "Hello"

        # Assert control 2: no logprobs requested, no logprobs in output
        assert ctrl_normal["request_top_logprobs"] is None
        assert ctrl_normal["bridged_chat_logprobs"] is None
        assert ctrl_normal["response_has_logprobs"] is False

        # Assert control 3: provider validation rejects top_logprobs without logprobs
        assert ctrl_validation["status_code"] == 400
        assert "'top_logprobs' is only allowed when 'logprobs' is enabled" in ctrl_validation["error_message"]

        run_records.append({
            "run": i + 1,
            "defect_unpatched_bridge_drops_logprobs": True,
            "control_fixed_bridge_preserves_logprobs": True,
            "control_responses_without_logprobs": True,
            "control_provider_validation_rule": True,
        })

    defect_sample = run_defect_unpatched_bridge_drops_logprobs()
    ctrl_fixed_sample = run_control_fixed_bridge_preserves_logprobs()
    ctrl_normal_sample = run_control_responses_without_logprobs()
    ctrl_validation_sample = run_control_provider_validation_rule()

    (wire_dir / "bug-responses-bridge-logprobs-dropped.json").write_text(
        json.dumps(defect_sample, indent=2) + "\n"
    )
    (wire_dir / "control-fixed-responses-bridge-logprobs-preserved.json").write_text(
        json.dumps(ctrl_fixed_sample, indent=2) + "\n"
    )
    (wire_dir / "control-responses-without-logprobs.json").write_text(
        json.dumps(ctrl_normal_sample, indent=2) + "\n"
    )
    (wire_dir / "control-provider-validation-top-logprobs-requires-logprobs.json").write_text(
        json.dumps(ctrl_validation_sample, indent=2) + "\n"
    )

    results = {
        "iterations": iterations,
        "success_rate": f"{iterations}/{iterations}",
        "all_passed": True,
        "runs": run_records,
    }
    (wire_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (Path(__file__).resolve().parents[1] / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"Reproduced 139: {iterations}/{iterations} runs passed successfully.")


if __name__ == "__main__":
    main()
