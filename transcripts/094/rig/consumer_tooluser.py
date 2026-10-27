#!/usr/bin/env python3
"""Consumer boundary for finding 094: Anthropic's own tool_use_package ToolUser.

Runs the package's calculator example tools (anthropics/anthropic-tools @ 795f706)
unmodified, with the official `anthropic` SDK pointed at the Dynamo frontend via
ANTHROPIC_BASE_URL. Every tool execution is appended to a ledger.

usage: consumer_tooluser.py <mode>
  mode=dynamo        real Dynamo /v1/messages response, untouched
  mode=counterfactual same Dynamo response, but stop_reason/stop_sequence replaced with
                      the values the live Anthropic API reports for a delimiter stop
                      (stop_sequence / the matched sequence). Isolates the two fields.
"""
import json, os, sys

sys.path.insert(0, "/tmp/anthropic-tools")
from tool_use_package.tool_user import ToolUser  # noqa: E402
from tool_use_package.tools.base_tool import BaseTool  # noqa: E402

LEDGER = os.environ.get("LEDGER", "/tmp/tooluser-ledger.jsonl")
mode = sys.argv[1]


class AdditionTool(BaseTool):
    """Adds together two numbers, a + b."""

    def use_tool(self, a, b):
        with open(LEDGER, "a") as f:
            f.write(json.dumps({"mode": mode, "tool": "perform_addition", "a": a, "b": b}) + "\n")
        return a + b


addition_tool = AdditionTool(
    "perform_addition",
    "Add one number (a) to another (b), returning a+b.",
    [{"name": "a", "type": "float", "description": "The first number."},
     {"name": "b", "type": "float", "description": "The second number."}],
)

tool_user = ToolUser([addition_tool], model="Qwen/Qwen3-0.6B", max_retries=2)

if mode == "counterfactual":
    real_create = tool_user.client.messages.create

    def create(**kw):
        msg = real_create(**kw)
        # Only change: report the delimiter stop the way the Messages API does.
        # The generated text is untouched.
        if msg.stop_reason == "end_turn" and "</function_calls>" in kw.get("stop_sequences", []) \
                and "<function_calls>" in (msg.content[0].text if msg.content else ""):
            msg.stop_reason = "stop_sequence"
            msg.stop_sequence = "</function_calls>"
        return msg

    tool_user.client.messages.create = create

messages = [{"role": "user", "content": "SCRIPT:xmltool What is 2 plus 3?"}]
try:
    result = tool_user.use_tools(messages, execution_mode="automatic")
    print("RESULT", json.dumps(result if isinstance(result, (str, dict, list)) else str(result))[:500])
except Exception as e:  # noqa: BLE001
    print("RAISED", type(e).__name__, str(e)[:500])
