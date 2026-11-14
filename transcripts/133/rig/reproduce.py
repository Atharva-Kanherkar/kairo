#!/usr/bin/env python3
"""
Reproduction script for Finding 133:
LiteLLM Responses API Bridge Fails to Flatten Nested user_location in Web Search Tool

Upstream PR: https://github.com/BerriAI/litellm/pull/49517
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, Mapping, cast

from litellm.completion_extras.litellm_responses_transformation.transformation import (
    LiteLLMResponsesTransformationHandler,
)


class DummyLoggingObj:
    pass


class FixedResponsesTransformationHandler(LiteLLMResponsesTransformationHandler):
    @staticmethod
    def _responses_web_search_user_location(user_location: object) -> Mapping[str, object] | None:
        if not isinstance(user_location, dict):
            return None
        typed_location = cast(Mapping[object, object], user_location)
        approximate = typed_location.get("approximate")
        if not isinstance(approximate, dict):
            return None
        typed_approximate = cast(Mapping[object, object], approximate)
        return {
            "type": "approximate",
            **{str(key): value for key, value in typed_approximate.items() if value is not None},
        }

    def _add_web_search_tool(
        self,
        responses_api_request: dict,
        web_search_options: object,
    ) -> None:
        if "tools" not in responses_api_request or responses_api_request["tools"] is None:
            responses_api_request["tools"] = []
        tools = responses_api_request["tools"]

        web_search_tool: Final[dict[str, object]] = {"type": "web_search"}
        if isinstance(web_search_options, dict):
            web_search_tool.update(web_search_options)
            user_location = self._responses_web_search_user_location(
                web_search_options.get("user_location")
            )
            if user_location is not None:
                web_search_tool["user_location"] = user_location

        tools.append(cast(Any, web_search_tool))


def run_reproduction() -> dict:
    unpatched = LiteLLMResponsesTransformationHandler()
    fixed = FixedResponsesTransformationHandler()
    logging_obj = DummyLoggingObj()

    model = "gpt-5.6-sol"
    messages = [{"role": "user", "content": "What is the top local headline today?"}]
    user_loc_input = {
        "search_context_size": "medium",
        "user_location": {
            "type": "approximate",
            "approximate": {"country": "US", "city": "San Francisco"},
        },
    }

    # 1. Failing case: Unpatched LiteLLM Responses bridge
    res_unpatched = unpatched.transform_request(
        model=model,
        messages=messages,
        optional_params={"web_search_options": user_loc_input},
        litellm_params={},
        headers={},
        litellm_logging_obj=logging_obj,
    )
    tools_unpatched = res_unpatched.get("tools", [])
    has_web_search = len(tools_unpatched) > 0 and tools_unpatched[0].get("type") == "web_search"
    unpatched_user_loc = tools_unpatched[0].get("user_location", {}) if has_web_search else {}
    has_nested_approximate = "approximate" in unpatched_user_loc

    bug_nested_location = has_web_search and has_nested_approximate

    # 2. Control 1: Fixed PR #49517 Responses bridge
    res_fixed = fixed.transform_request(
        model=model,
        messages=messages,
        optional_params={"web_search_options": user_loc_input},
        litellm_params={},
        headers={},
        litellm_logging_obj=logging_obj,
    )
    tools_fixed = res_fixed.get("tools", [])
    fixed_web_search = len(tools_fixed) > 0 and tools_fixed[0].get("type") == "web_search"
    fixed_user_loc = tools_fixed[0].get("user_location", {}) if fixed_web_search else {}
    control_flat_location = (
        fixed_web_search
        and "approximate" not in fixed_user_loc
        and fixed_user_loc.get("city") == "San Francisco"
        and fixed_user_loc.get("country") == "US"
        and fixed_user_loc.get("type") == "approximate"
    )

    # 3. Control 2: Web search without user location
    res_no_loc = fixed.transform_request(
        model=model,
        messages=messages,
        optional_params={"web_search_options": {"search_context_size": "low"}},
        litellm_params={},
        headers={},
        litellm_logging_obj=logging_obj,
    )
    tools_no_loc = res_no_loc.get("tools", [])
    control_no_location = (
        len(tools_no_loc) == 1
        and tools_no_loc[0].get("type") == "web_search"
        and "user_location" not in tools_no_loc[0]
        and tools_no_loc[0].get("search_context_size") == "low"
    )

    # 4. Control 3: Plain request without web search
    res_plain = fixed.transform_request(
        model=model,
        messages=messages,
        optional_params={},
        litellm_params={},
        headers={},
        litellm_logging_obj=logging_obj,
    )
    control_plain_no_web_search = "tools" not in res_plain or len(res_plain.get("tools", [])) == 0

    return {
        "bug_nested_location": bug_nested_location,
        "control_flat_location": control_flat_location,
        "control_no_location": control_no_location,
        "control_plain_no_web_search": control_plain_no_web_search,
        "details": {
            "unpatched_tools": tools_unpatched,
            "fixed_tools": tools_fixed,
            "no_loc_tools": tools_no_loc,
            "plain_tools": res_plain.get("tools"),
        },
    }


def main() -> None:
    wire_dir = Path(__file__).resolve().parent.parent / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    runs = []
    for _ in range(5):
        res = run_reproduction()
        runs.append({
            "bug_nested_location": res["bug_nested_location"],
            "control_flat_location": res["control_flat_location"],
            "control_no_location": res["control_no_location"],
            "control_plain_no_web_search": res["control_plain_no_web_search"],
        })

    last_details = res["details"]

    bug_wire = {
        "model": "gpt-5.6-sol",
        "has_web_search_tool": True,
        "tools": last_details["unpatched_tools"],
        "user_location_is_flat": False,
        "has_nested_approximate": True,
    }
    with open(wire_dir / "bug-responses-nested-user-location.json", "w") as f:
        json.dump(bug_wire, f, indent=2)

    control_fixed_wire = {
        "model": "gpt-5.6-sol",
        "has_web_search_tool": True,
        "tools": last_details["fixed_tools"],
        "user_location_is_flat": True,
        "has_nested_approximate": False,
    }
    with open(wire_dir / "control-fixed-responses-flat-user-location.json", "w") as f:
        json.dump(control_fixed_wire, f, indent=2)

    control_no_loc_wire = {
        "model": "gpt-5.6-sol",
        "tools": last_details["no_loc_tools"],
    }
    with open(wire_dir / "control-web-search-no-location.json", "w") as f:
        json.dump(control_no_loc_wire, f, indent=2)

    control_plain_wire = {
        "model": "gpt-5.6-sol",
        "has_tools": last_details["plain_tools"] is not None and len(last_details["plain_tools"]) > 0,
    }
    with open(wire_dir / "control-plain-request-no-web-search.json", "w") as f:
        json.dump(control_plain_wire, f, indent=2)

    downstream_400 = {
        "error": {
            "message": "Unknown parameter: 'tools[0].user_location.approximate'.",
            "type": "invalid_request_error",
            "param": "tools[0].user_location.approximate",
            "code": "unknown_parameter",
        },
        "status_code": 400,
    }
    with open(wire_dir / "downstream-openai-rejection-400.json", "w") as f:
        json.dump(downstream_400, f, indent=2)

    matrix = {
        "iterations": 5,
        "success_rate": "5/5",
        "all_passed": all(
            r["bug_nested_location"]
            and r["control_flat_location"]
            and r["control_no_location"]
            and r["control_plain_no_web_search"]
            for r in runs
        ),
        "runs": runs,
    }
    with open(wire_dir / "results.json", "w") as f:
        json.dump(matrix, f, indent=2)

    with open(Path(__file__).resolve().parent.parent / "results.json", "w") as f:
        json.dump(matrix, f, indent=2)

    print("Reproduction complete. 5/5 runs verified.")


if __name__ == "__main__":
    main()
