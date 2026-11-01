#!/usr/bin/env python3
"""Reproduction rig for Finding 137: LiteLLM GithubCopilotConfig passes

system messages with list-form content through unflattened, causing
the Copilot backend to ignore system instructions.

Target: LiteLLM v1.109.0 (commit cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a / main 60fc121ce8a)
Upstream Issue: https://github.com/BerriAI/litellm/issues/48568
Upstream PR: https://github.com/BerriAI/litellm/pull/48863
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from litellm.llms.github_copilot.chat.transformation import GithubCopilotConfig


def _flatten_text_content_blocks(
    content: Any,
) -> Optional[Tuple[str, Optional[Any]]]:
    """If content is a list of text-only blocks, returns (flattened_text, cache_control).

    Leaves multimodal or non-text content (e.g. image_url) untouched by returning None.
    """
    if not isinstance(content, list):
        return None

    extracted_texts: List[str] = []
    cache_control: Optional[Any] = None

    for item in content:
        if isinstance(item, str):
            extracted_texts.append(item)
        elif isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "text" or (item_type is None and "text" in item):
                text_val = item.get("text")
                if isinstance(text_val, str):
                    extracted_texts.append(text_val)
                if cache_control is None and "cache_control" in item:
                    cache_control = item.get("cache_control")
            else:
                return None
        else:
            return None

    return "".join(extracted_texts), cache_control


class PatchedGithubCopilotConfig(GithubCopilotConfig):
    """Patched GithubCopilotConfig applying PR #48863 text-block flattening."""
    def _transform_messages(self, messages, model):
        import litellm
        from typing import cast
        disable_conversion = bool(getattr(litellm, "disable_copilot_system_to_assistant", False))
        transformed_messages = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                target_role = "system" if disable_conversion else "assistant"
                flattened = _flatten_text_content_blocks(message.get("content"))
                if flattened is not None:
                    flattened_content, cache_control = flattened
                    msg = {
                        **message,
                        "role": target_role,
                        "content": flattened_content,
                    }
                    if cache_control is not None and "cache_control" not in message:
                        msg["cache_control"] = cache_control
                    transformed_messages.append(msg)
                else:
                    transformed_messages.append({**message, "role": target_role})
            else:
                transformed_messages.append(message)
        return transformed_messages


def run_defect_list_content() -> Dict[str, Any]:
    config = GithubCopilotConfig()
    messages = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "Always talk like a pirate."},
                {"type": "text", "text": " Ahoy!"},
            ],
        },
        {"role": "user", "content": "Where is the treasure?"},
    ]

    transformed = config.transform_request("github_copilot/gpt-4o", messages, {}, {}, {})
    system_msg = transformed["messages"][0]
    content = system_msg["content"]

    return {
        "model": transformed.get("model"),
        "role": system_msg.get("role"),
        "content_is_list": isinstance(content, list),
        "content_is_str": isinstance(content, str),
        "content": content,
    }


def run_control_plain_string() -> Dict[str, Any]:
    config = GithubCopilotConfig()
    messages = [
        {
            "role": "system",
            "content": "Always talk like a pirate. Ahoy!",
        },
        {"role": "user", "content": "Where is the treasure?"},
    ]

    transformed = config.transform_request("github_copilot/gpt-4o", messages, {}, {}, {})
    system_msg = transformed["messages"][0]
    content = system_msg["content"]

    return {
        "model": transformed.get("model"),
        "role": system_msg.get("role"),
        "content_is_list": isinstance(content, list),
        "content_is_str": isinstance(content, str),
        "content": content,
    }


def run_control_fixed_list_content() -> Dict[str, Any]:
    config = PatchedGithubCopilotConfig()
    messages = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "Always talk like a pirate."},
                {"type": "text", "text": " Ahoy!"},
            ],
        },
        {"role": "user", "content": "Where is the treasure?"},
    ]

    transformed = config.transform_request("github_copilot/gpt-4o", messages, {}, {}, {})
    system_msg = transformed["messages"][0]
    content = system_msg["content"]

    return {
        "model": transformed.get("model"),
        "role": system_msg.get("role"),
        "content_is_list": isinstance(content, list),
        "content_is_str": isinstance(content, str),
        "content": content,
    }


def run_control_cache_control_promotion() -> Dict[str, Any]:
    config = PatchedGithubCopilotConfig()
    messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "Context prompt.",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {"role": "user", "content": "Hello"},
    ]

    transformed = config.transform_request("github_copilot/gpt-4o", messages, {}, {}, {})
    system_msg = transformed["messages"][0]
    content = system_msg["content"]

    return {
        "model": transformed.get("model"),
        "role": system_msg.get("role"),
        "content_is_list": isinstance(content, list),
        "content_is_str": isinstance(content, str),
        "content": content,
        "cache_control": system_msg.get("cache_control"),
    }


def main():
    wire_dir = Path(__file__).resolve().parents[1] / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    iterations = 5
    run_records = []

    for i in range(iterations):
        defect = run_defect_list_content()
        ctrl_plain = run_control_plain_string()
        ctrl_fixed = run_control_fixed_list_content()
        ctrl_cache = run_control_cache_control_promotion()

        assert defect["content_is_list"] is True
        assert defect["content_is_str"] is False

        assert ctrl_plain["content_is_list"] is False
        assert ctrl_plain["content_is_str"] is True
        assert ctrl_plain["content"] == "Always talk like a pirate. Ahoy!"

        assert ctrl_fixed["content_is_list"] is False
        assert ctrl_fixed["content_is_str"] is True
        assert ctrl_fixed["content"] == "Always talk like a pirate. Ahoy!"

        assert ctrl_cache["content_is_str"] is True
        assert ctrl_cache["cache_control"] == {"type": "ephemeral"}

        run_records.append({
            "run": i + 1,
            "defect_emits_unflattened_list": True,
            "control_plain_string_preserved": True,
            "control_fixed_flattens_list": True,
            "control_promotes_cache_control": True,
        })

    defect_sample = run_defect_list_content()
    ctrl_plain_sample = run_control_plain_string()
    ctrl_fixed_sample = run_control_fixed_list_content()
    ctrl_cache_sample = run_control_cache_control_promotion()

    (wire_dir / "bug-github-copilot-system-list-content-unflattened.json").write_text(
        json.dumps(defect_sample, indent=2) + "\n"
    )
    (wire_dir / "control-plain-string-system-content.json").write_text(
        json.dumps(ctrl_plain_sample, indent=2) + "\n"
    )
    (wire_dir / "control-fixed-system-list-content-flattened.json").write_text(
        json.dumps(ctrl_fixed_sample, indent=2) + "\n"
    )
    (wire_dir / "control-cache-control-promoted.json").write_text(
        json.dumps(ctrl_cache_sample, indent=2) + "\n"
    )

    results = {
        "iterations": iterations,
        "success_rate": f"{iterations}/{iterations}",
        "all_passed": True,
        "runs": run_records,
    }
    (wire_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (Path(__file__).resolve().parents[1] / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"Reproduced 137: {iterations}/{iterations} runs passed successfully.")


if __name__ == "__main__":
    main()
