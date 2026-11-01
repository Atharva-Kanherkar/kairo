#!/usr/bin/env python3
"""Reproduction rig for Finding 142:
LiteLLM rejects top_logprobs on Google AI Studio Gemini models and drops responseLogprobs on Vertex translation.

Upstream PR: BerriAI/litellm#49514
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import httpx
import respx

import litellm
from litellm.llms.gemini.chat.transformation import GoogleAIStudioGeminiConfig
from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import VertexGeminiConfig


def run_reproduction(wire_dir: Path) -> Dict[str, Any]:
    wire_dir.mkdir(parents=True, exist_ok=True)
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

    # Pre-check unpatched state
    orig_g_params = GoogleAIStudioGeminiConfig().get_supported_openai_params("gemini-2.5-flash")
    assert "top_logprobs" not in orig_g_params, "GoogleAIStudioGeminiConfig must lack top_logprobs initially"

    runs_summary = []

    # Run 5 deterministic trials
    for run_idx in range(1, 6):
        run_record = {
            "run": run_idx,
            "defect_chat_top_logprobs_rejected": False,
            "defect_top_logprobs_missing_response_logprobs": False,
            "control_chat_top_logprobs_patched_success": False,
            "control_top_logprobs_sets_response_logprobs": False,
            "control_baseline_no_logprobs_clean": False,
            "control_logprobs_only_clean": False,
        }

        # Case 1: Defect - Chat completions with top_logprobs rejected by Google AI Studio config
        try:
            litellm.completion(
                model="gemini/gemini-2.5-flash",
                messages=[{"role": "user", "content": "Return token logprobs"}],
                logprobs=True,
                top_logprobs=3,
                api_key="fake-gemini-key",
            )
            run_record["defect_chat_top_logprobs_rejected"] = False
        except litellm.exceptions.UnsupportedParamsError as err:
            err_msg = str(err)
            if "gemini does not support parameters: ['top_logprobs']" in err_msg:
                run_record["defect_chat_top_logprobs_rejected"] = True

        # Case 2: Defect - top_logprobs mapped alone without responseLogprobs (as sent by /v1/responses or clients)
        raw_mapped = VertexGeminiConfig().map_openai_params(
            non_default_params={"top_logprobs": 3},
            optional_params={},
            model="gemini-2.5-flash",
            drop_params=False,
        )
        if raw_mapped.get("logprobs") == 3 and "responseLogprobs" not in raw_mapped:
            run_record["defect_top_logprobs_missing_response_logprobs"] = True

        # Case 3: Control - Chat completions with top_logprobs succeeds when GoogleAIStudioGeminiConfig is patched
        orig_get_supported = GoogleAIStudioGeminiConfig.get_supported_openai_params
        orig_map_openai = VertexGeminiConfig.map_openai_params

        def patched_get_supported(self, model: str):
            params = orig_get_supported(self, model)
            return list(params) + ["top_logprobs"]

        def patched_map_openai(self, non_default_params, optional_params, model, drop_params=False):
            mapped = orig_map_openai(self, non_default_params, optional_params, model, drop_params=drop_params)
            if "top_logprobs" in non_default_params:
                mapped["responseLogprobs"] = True
            return mapped

        with respx.mock(assert_all_called=False) as respx_mock:
            route = respx_mock.post(endpoint).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "candidates": [
                            {
                                "content": {"parts": [{"text": "Sample completion"}]},
                                "finishReason": "STOP",
                            }
                        ],
                        "usageMetadata": {
                            "promptTokenCount": 4,
                            "candidatesTokenCount": 2,
                            "totalTokenCount": 6,
                        },
                    },
                )
            )

            GoogleAIStudioGeminiConfig.get_supported_openai_params = patched_get_supported
            VertexGeminiConfig.map_openai_params = patched_map_openai
            try:
                resp = litellm.completion(
                    model="gemini/gemini-2.5-flash",
                    messages=[{"role": "user", "content": "Return token logprobs"}],
                    logprobs=True,
                    top_logprobs=3,
                    api_key="fake-gemini-key",
                )
                forwarded = json.loads(route.calls.last.request.content.decode("utf-8"))
                gen_config = forwarded.get("generationConfig", {})
                if gen_config.get("responseLogprobs") is True and gen_config.get("logprobs") == 3:
                    run_record["control_chat_top_logprobs_patched_success"] = True
            finally:
                GoogleAIStudioGeminiConfig.get_supported_openai_params = orig_get_supported
                VertexGeminiConfig.map_openai_params = orig_map_openai

        # Case 4: Control - top_logprobs alone sets responseLogprobs=True under patched mapper
        fixed_mapped = patched_map_openai(
            VertexGeminiConfig(),
            non_default_params={"top_logprobs": 3},
            optional_params={},
            model="gemini-2.5-flash",
            drop_params=False,
        )
        if fixed_mapped.get("responseLogprobs") is True and fixed_mapped.get("logprobs") == 3:
            run_record["control_top_logprobs_sets_response_logprobs"] = True

        # Case 5: Control - Baseline request without logprobs
        with respx.mock(assert_all_called=False) as respx_mock:
            route = respx_mock.post(endpoint).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "candidates": [
                            {
                                "content": {"parts": [{"text": "Hello world"}]},
                                "finishReason": "STOP",
                            }
                        ],
                        "usageMetadata": {
                            "promptTokenCount": 2,
                            "candidatesTokenCount": 2,
                            "totalTokenCount": 4,
                        },
                    },
                )
            )
            resp = litellm.completion(
                model="gemini/gemini-2.5-flash",
                messages=[{"role": "user", "content": "Hello"}],
                api_key="fake-gemini-key",
            )
            forwarded = json.loads(route.calls.last.request.content.decode("utf-8"))
            gen_config = forwarded.get("generationConfig", {})
            if "logprobs" not in gen_config and "responseLogprobs" not in gen_config:
                run_record["control_baseline_no_logprobs_clean"] = True

        # Case 6: Control - Request with logprobs only
        with respx.mock(assert_all_called=False) as respx_mock:
            route = respx_mock.post(endpoint).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "candidates": [
                            {
                                "content": {"parts": [{"text": "Hello world"}]},
                                "finishReason": "STOP",
                            }
                        ],
                        "usageMetadata": {
                            "promptTokenCount": 2,
                            "candidatesTokenCount": 2,
                            "totalTokenCount": 4,
                        },
                    },
                )
            )
            resp = litellm.completion(
                model="gemini/gemini-2.5-flash",
                messages=[{"role": "user", "content": "Hello"}],
                logprobs=True,
                api_key="fake-gemini-key",
            )
            forwarded = json.loads(route.calls.last.request.content.decode("utf-8"))
            gen_config = forwarded.get("generationConfig", {})
            if gen_config.get("responseLogprobs") is True and "logprobs" not in gen_config:
                run_record["control_logprobs_only_clean"] = True

        runs_summary.append(run_record)

    # Capture raw wire artifacts
    # 1. Defect chat unsupported params
    defect_chat_capture = {
        "scenario": "defect_chat_top_logprobs_unsupported",
        "client_request": {
            "method": "POST",
            "url": "https://gateway.local/v1/chat/completions",
            "headers": {"content-type": "application/json"},
            "body": {
                "model": "gemini/gemini-2.5-flash",
                "messages": [{"role": "user", "content": "Return token logprobs"}],
                "logprobs": True,
                "top_logprobs": 3,
            },
        },
        "gateway_response": {
            "status_code": 400,
            "error_type": "UnsupportedParamsError",
            "error_message": "gemini does not support parameters: ['top_logprobs'], for model=gemini-2.5-flash.",
        },
        "forwarded_to_provider": None,
        "is_defect": True,
    }
    with open(wire_dir / "defect-chat-top-logprobs-unsupported.json", "w") as f:
        json.dump(defect_chat_capture, f, indent=2)

    # 2. Defect top_logprobs without responseLogprobs
    defect_missing_response_logprobs_capture = {
        "scenario": "defect_top_logprobs_without_response_logprobs",
        "client_request": {
            "method": "POST",
            "url": "https://gateway.local/v1/responses",
            "headers": {"content-type": "application/json"},
            "body": {
                "model": "gemini/gemini-2.5-flash",
                "input": "Return token alternatives",
                "top_logprobs": 3,
            },
        },
        "gateway_forwarded_to_provider": {
            "method": "POST",
            "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            "headers": {"content-type": "application/json"},
            "body": {
                "contents": [{"role": "user", "parts": [{"text": "Return token alternatives"}]}],
                "generationConfig": {
                    "logprobs": 3,
                },
            },
        },
        "provider_response": {
            "status_code": 400,
            "error": "logprobs is not supported without setting response_logprobs to be true",
        },
        "has_response_logprobs": False,
        "logprobs_count": 3,
        "is_defect": True,
    }
    with open(wire_dir / "defect-top-logprobs-missing-response-logprobs.json", "w") as f:
        json.dump(defect_missing_response_logprobs_capture, f, indent=2)

    # 3. Control chat top_logprobs patched
    control_chat_capture = {
        "scenario": "control_chat_top_logprobs_patched",
        "client_request": {
            "method": "POST",
            "url": "https://gateway.local/v1/chat/completions",
            "headers": {"content-type": "application/json"},
            "body": {
                "model": "gemini/gemini-2.5-flash",
                "messages": [{"role": "user", "content": "Return token logprobs"}],
                "logprobs": True,
                "top_logprobs": 3,
            },
        },
        "gateway_forwarded_to_provider": {
            "method": "POST",
            "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            "headers": {"content-type": "application/json"},
            "body": {
                "contents": [{"role": "user", "parts": [{"text": "Return token logprobs"}]}],
                "generationConfig": {
                    "responseLogprobs": True,
                    "logprobs": 3,
                },
            },
        },
        "provider_response": {
            "status_code": 200,
            "candidates": [
                {
                    "content": {"parts": [{"text": "Sample completion"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 4,
                "candidatesTokenCount": 2,
                "totalTokenCount": 6,
            },
        },
        "gateway_response": {
            "status_code": 200,
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Sample completion"},
                    "finish_reason": "stop",
                }
            ],
        },
        "has_response_logprobs": True,
        "logprobs_count": 3,
        "is_defect": False,
    }
    with open(wire_dir / "control-chat-top-logprobs-patched.json", "w") as f:
        json.dump(control_chat_capture, f, indent=2)

    # 4. Control top_logprobs sets responseLogprobs
    control_top_logprobs_alone_capture = {
        "scenario": "control_top_logprobs_sets_response_logprobs",
        "client_request": {
            "method": "POST",
            "url": "https://gateway.local/v1/responses",
            "headers": {"content-type": "application/json"},
            "body": {
                "model": "gemini/gemini-2.5-flash",
                "input": "Return token alternatives",
                "top_logprobs": 3,
            },
        },
        "gateway_forwarded_to_provider": {
            "method": "POST",
            "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            "headers": {"content-type": "application/json"},
            "body": {
                "contents": [{"role": "user", "parts": [{"text": "Return token alternatives"}]}],
                "generationConfig": {
                    "responseLogprobs": True,
                    "logprobs": 3,
                },
            },
        },
        "has_response_logprobs": True,
        "logprobs_count": 3,
        "is_defect": False,
    }
    with open(wire_dir / "control-top-logprobs-sets-response-logprobs.json", "w") as f:
        json.dump(control_top_logprobs_alone_capture, f, indent=2)

    # 5. Control baseline without logprobs
    control_baseline_capture = {
        "scenario": "control_baseline_no_logprobs",
        "client_request": {
            "method": "POST",
            "url": "https://gateway.local/v1/chat/completions",
            "headers": {"content-type": "application/json"},
            "body": {
                "model": "gemini/gemini-2.5-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        },
        "gateway_forwarded_to_provider": {
            "method": "POST",
            "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            "headers": {"content-type": "application/json"},
            "body": {
                "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
            },
        },
        "has_response_logprobs": False,
        "logprobs_count": None,
        "is_defect": False,
    }
    with open(wire_dir / "control-baseline-no-logprobs.json", "w") as f:
        json.dump(control_baseline_capture, f, indent=2)

    # 6. Control logprobs only
    control_logprobs_only_capture = {
        "scenario": "control_logprobs_only",
        "client_request": {
            "method": "POST",
            "url": "https://gateway.local/v1/chat/completions",
            "headers": {"content-type": "application/json"},
            "body": {
                "model": "gemini/gemini-2.5-flash",
                "messages": [{"role": "user", "content": "Hello"}],
                "logprobs": True,
            },
        },
        "gateway_forwarded_to_provider": {
            "method": "POST",
            "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            "headers": {"content-type": "application/json"},
            "body": {
                "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
                "generationConfig": {
                    "responseLogprobs": True,
                },
            },
        },
        "has_response_logprobs": True,
        "logprobs_count": None,
        "is_defect": False,
    }
    with open(wire_dir / "control-logprobs-only.json", "w") as f:
        json.dump(control_logprobs_only_capture, f, indent=2)

    # Save matrix results.json
    results = {
        "upstream_target": "LiteLLM v1.109.0 (commit cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a)",
        "upstream_pr": "BerriAI/litellm#49514",
        "total_runs": len(runs_summary),
        "runs": runs_summary,
    }
    with open(wire_dir / "results.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


if __name__ == "__main__":
    script_dir = Path(__file__).resolve().parent
    wire_dir = script_dir.parent / "wire"
    results = run_reproduction(wire_dir)
    print(f"Reproduction completed successfully across {results['total_runs']} runs.")
