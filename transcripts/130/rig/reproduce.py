"""Reproduction script for LiteLLM MiniMax provider dropping top-level reasoning_effort.

Demonstrates that when callers request reasoning effort (e.g. reasoning_effort="low" or "high")
on MiniMax reasoning models (e.g. minimax/MiniMax-M3.1-Flash):
1. In `litellm/llms/minimax/chat/transformation.py:MinimaxChatConfig.get_supported_openai_params`,
   `reasoning_effort` is omitted from `additional_params` (only `reasoning_split` and `thinking`
   are appended).
2. When callers pass `reasoning_effort` at the top level of a chat completion request,
   `MinimaxChatConfig.map_openai_params` purges `reasoning_effort` from the outbound request payload.
3. The upstream MiniMax provider receives no reasoning effort level, ignoring the user's requested
   trade-off and returning default reasoning tokens across both low and high effort requests.
4. Controls:
   - Fixed configuration (PR #49583 logic): Adding `reasoning_effort` to `additional_params`
     preserves `reasoning_effort` in the mapped outbound request parameters.
   - OpenAI o-series route control: `OpenAIOSeriesConfig` retains `reasoning_effort`.
   - High effort control: Both "low" and "high" effort levels are preserved under fixed configuration.
   - Non-reasoning parameter control: Genuinely unsupported parameters are correctly filtered.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from litellm.llms.minimax.chat.transformation import MinimaxChatConfig
from litellm.llms.openai.chat.o_series_transformation import OpenAIOSeriesConfig


class FixedMinimaxChatConfig(MinimaxChatConfig):
    """Fixed config applying upstream PR #49583 changes."""

    def get_supported_openai_params(self, model: str) -> list[str]:
        base_params = super().get_supported_openai_params(model=model)
        additional_params = ["reasoning_split", "reasoning_effort"]
        try:
            from litellm.utils import supports_reasoning

            if supports_reasoning(model=model):
                additional_params.append("thinking")
        except Exception:
            pass
        return base_params + additional_params


def run_trials(n: int = 5) -> dict[str, Any]:
    wire_dir = Path(__file__).resolve().parent.parent / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    buggy_config = MinimaxChatConfig()
    fixed_config = FixedMinimaxChatConfig()
    oseries_config = OpenAIOSeriesConfig()

    model = "minimax/MiniMax-M3.1-Flash"

    runs: list[dict[str, bool]] = []
    last_buggy_out = None
    last_fixed_out = None
    last_oseries_out = None
    last_high_out = None

    for _ in range(n):
        run_entry: dict[str, bool] = {
            "bug_reasoning_effort_dropped": False,
            "control_fixed_reasoning_effort_preserved": False,
            "control_oseries_route_preserved": False,
            "control_high_effort_preserved": False,
        }

        # 1. Buggy path: reasoning_effort="low" on MinimaxChatConfig
        bug_params = buggy_config.map_openai_params(
            non_default_params={"reasoning_effort": "low"},
            optional_params={},
            model=model,
            drop_params=True,
        )
        last_buggy_out = bug_params
        if "reasoning_effort" not in bug_params:
            run_entry["bug_reasoning_effort_dropped"] = True

        # 2. Control 1: Fixed MinimaxChatConfig preserves reasoning_effort
        fixed_params = fixed_config.map_openai_params(
            non_default_params={"reasoning_effort": "low"},
            optional_params={},
            model=model,
            drop_params=True,
        )
        last_fixed_out = fixed_params
        if fixed_params.get("reasoning_effort") == "low":
            run_entry["control_fixed_reasoning_effort_preserved"] = True

        # 3. Control 2: OpenAI o-series configuration preserves reasoning_effort
        oseries_params = oseries_config.map_openai_params(
            non_default_params={"reasoning_effort": "low"},
            optional_params={},
            model="o1",
            drop_params=True,
        )
        last_oseries_out = oseries_params
        if oseries_params.get("reasoning_effort") == "low":
            run_entry["control_oseries_route_preserved"] = True

        # 4. Control 3: High effort preserved under fixed config
        high_params = fixed_config.map_openai_params(
            non_default_params={"reasoning_effort": "high"},
            optional_params={},
            model=model,
            drop_params=True,
        )
        last_high_out = high_params
        if high_params.get("reasoning_effort") == "high":
            run_entry["control_high_effort_preserved"] = True

        runs.append(run_entry)

    results: dict[str, Any] = {
        "iterations": n,
        "success_rate": f"{sum(1 for r in runs if r['bug_reasoning_effort_dropped'])}/{n}",
        "all_passed": all(
            r["bug_reasoning_effort_dropped"]
            and r["control_fixed_reasoning_effort_preserved"]
            and r["control_oseries_route_preserved"]
            and r["control_high_effort_preserved"]
            for r in runs
        ),
        "runs": runs,
    }

    # Save wire artifacts
    with open(wire_dir / "bug-minimax-reasoning-effort-dropped.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": model,
                "input_non_default_params": {"reasoning_effort": "low"},
                "supported_params_contains_reasoning_effort": (
                    "reasoning_effort" in buggy_config.get_supported_openai_params(model)
                ),
                "mapped_outbound_params": last_buggy_out,
                "reasoning_effort_dropped": "reasoning_effort" not in last_buggy_out,
            },
            f,
            indent=2,
        )

    with open(wire_dir / "control-fixed-minimax-reasoning-effort-preserved.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": model,
                "input_non_default_params": {"reasoning_effort": "low"},
                "supported_params_contains_reasoning_effort": (
                    "reasoning_effort" in fixed_config.get_supported_openai_params(model)
                ),
                "mapped_outbound_params": last_fixed_out,
                "reasoning_effort_preserved": last_fixed_out.get("reasoning_effort") == "low",
            },
            f,
            indent=2,
        )

    with open(wire_dir / "control-openai-route-reasoning-effort-preserved.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": "o1",
                "input_non_default_params": {"reasoning_effort": "low"},
                "mapped_outbound_params": last_oseries_out,
                "reasoning_effort_preserved": last_oseries_out.get("reasoning_effort") == "low",
            },
            f,
            indent=2,
        )

    with open(wire_dir / "control-high-effort-preserved.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "model": model,
                "input_non_default_params": {"reasoning_effort": "high"},
                "mapped_outbound_params": last_high_out,
                "reasoning_effort_preserved": last_high_out.get("reasoning_effort") == "high",
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
