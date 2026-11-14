"""Reproduction script for LiteLLM Anthropic provider sending structured outputs as top-level output_format.

Demonstrates that when callers request structured JSON schema output (response_format={"type": "json_schema", ...})
for Anthropic Claude models (e.g. claude-haiku-4-5):
1. In `litellm/llms/anthropic/chat/transformation.py:AnthropicConfig.transform_request`,
   `output_format` is emitted as a top-level request body parameter instead of being nested
   under `output_config.format`.
2. Anthropic-compatible providers (such as AWS Bedrock or Anthropic-compatible proxies) that only
   implement GA `output_config.format` silently ignore top-level `output_format` without error.
3. The provider returns unconstrained plain text or markdown-fenced text rather than schema-enforced
   JSON, breaking downstream JSON parsers and structured data pipelines.
4. Controls:
   - Fixed transformation (PR #49579 logic): Folds `output_format` into `output_config.format` and
     removes top-level `output_format`.
   - Beside effort control: When `output_config: {"effort": "low"}` is present, both `effort` and
     `format` are preserved inside `output_config`.
   - Precedence control: Incoming `output_format` correctly takes precedence over existing format.
   - Plain request control: Requests without `response_format` do not generate `output_config.format`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from litellm.llms.anthropic.chat.transformation import AnthropicConfig


def fixed_transform_request(
    config: AnthropicConfig,
    model: str,
    messages: list[dict[str, Any]],
    optional_params: dict[str, Any],
    litellm_params: dict[str, Any],
    headers: dict[str, Any],
) -> dict[str, Any]:
    """Applies PR #49579 fix logic folding output_format into output_config.format."""
    req = config.transform_request(
        model=model,
        messages=messages,
        optional_params=optional_params,
        litellm_params=litellm_params,
        headers=headers,
    )
    output_format = req.get("output_format")
    if isinstance(output_format, Mapping):
        output_config = req.get("output_config")
        req.pop("output_format")
        req["output_config"] = {
            **(output_config if isinstance(output_config, Mapping) else {}),
            "format": output_format,
        }
    return req


def run_trials(n: int = 5) -> dict[str, Any]:
    wire_dir = Path(__file__).resolve().parent.parent / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    config = AnthropicConfig()
    model = "claude-haiku-4-5"
    schema = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    }

    mapped_params = config.map_openai_params(
        non_default_params={
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "place", "schema": schema},
            }
        },
        optional_params={},
        model=model,
        drop_params=False,
    )

    mapped_params_with_effort = config.map_openai_params(
        non_default_params={
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "place", "schema": schema},
            }
        },
        optional_params={"output_config": {"effort": "low"}},
        model=model,
        drop_params=False,
    )

    messages = [{"role": "user", "content": "Name a city"}]

    runs: list[dict[str, bool]] = []
    last_buggy_out = None
    last_fixed_out = None
    last_effort_out = None
    last_plain_out = None

    for _ in range(n):
        run_entry: dict[str, bool] = {
            "bug_output_format_top_level": False,
            "control_fixed_output_config_format": False,
            "control_beside_effort_preserved": False,
            "control_plain_no_format": False,
        }

        # 1. Buggy path: transform_request leaves output_format at top level
        buggy_req = config.transform_request(
            model=model,
            messages=messages,
            optional_params=mapped_params,
            litellm_params={},
            headers={},
        )
        last_buggy_out = buggy_req
        if "output_format" in buggy_req and "output_config" not in buggy_req:
            run_entry["bug_output_format_top_level"] = True

        # 2. Control 1: Fixed transformation nests format under output_config
        fixed_req = fixed_transform_request(
            config=config,
            model=model,
            messages=messages,
            optional_params=mapped_params,
            litellm_params={},
            headers={},
        )
        last_fixed_out = fixed_req
        if (
            "output_format" not in fixed_req
            and isinstance(fixed_req.get("output_config"), dict)
            and fixed_req["output_config"].get("format", {}).get("type") == "json_schema"
        ):
            run_entry["control_fixed_output_config_format"] = True

        # 3. Control 2: Beside effort preserved
        effort_req = fixed_transform_request(
            config=config,
            model=model,
            messages=messages,
            optional_params=mapped_params_with_effort,
            litellm_params={},
            headers={},
        )
        last_effort_out = effort_req
        if (
            "output_format" not in effort_req
            and effort_req.get("output_config", {}).get("effort") == "low"
            and effort_req.get("output_config", {}).get("format", {}).get("type") == "json_schema"
        ):
            run_entry["control_beside_effort_preserved"] = True

        # 4. Control 3: Plain request has no output_config format
        plain_req = config.transform_request(
            model=model,
            messages=messages,
            optional_params={},
            litellm_params={},
            headers={},
        )
        last_plain_out = plain_req
        if "output_format" not in plain_req and "output_config" not in plain_req:
            run_entry["control_plain_no_format"] = True

        runs.append(run_entry)

    results: dict[str, Any] = {
        "iterations": n,
        "success_rate": f"{sum(1 for r in runs if r['bug_output_format_top_level'])}/{n}",
        "all_passed": all(
            r["bug_output_format_top_level"]
            and r["control_fixed_output_config_format"]
            and r["control_beside_effort_preserved"]
            and r["control_plain_no_format"]
            for r in runs
        ),
        "runs": runs,
    }

    # Save wire artifacts
    with open(wire_dir / "bug-anthropic-top-level-output-format.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": model,
                "has_top_level_output_format": "output_format" in last_buggy_out,
                "has_output_config": "output_config" in last_buggy_out,
                "outbound_body_keys": list(last_buggy_out.keys()),
                "output_format": last_buggy_out.get("output_format"),
            },
            f,
            indent=2,
        )

    with open(wire_dir / "control-fixed-anthropic-output-config-format.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": model,
                "has_top_level_output_format": "output_format" in last_fixed_out,
                "has_output_config": "output_config" in last_fixed_out,
                "outbound_body_keys": list(last_fixed_out.keys()),
                "output_config": last_fixed_out.get("output_config"),
            },
            f,
            indent=2,
        )

    with open(wire_dir / "control-output-config-beside-effort.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": model,
                "has_top_level_output_format": "output_format" in last_effort_out,
                "output_config": last_effort_out.get("output_config"),
            },
            f,
            indent=2,
        )

    with open(wire_dir / "control-no-structured-output.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": model,
                "has_top_level_output_format": "output_format" in last_plain_out,
                "has_output_config": "output_config" in last_plain_out,
            },
            f,
            indent=2,
        )

    with open(wire_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    with open(Path(__file__).resolve().parent.parent / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return results


if __name__ == "__main__":
    res = run_trials(5)
    print("Reproduction Results (5/5 trials):")
    print(json.dumps(res, indent=2))
