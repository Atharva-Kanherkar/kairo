"""Hidden verifier (no-bug task): is_error already maps to function_call_output status."""

import json
import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import bifrost  # noqa: E402
from upstream import respond  # noqa: E402

R = Results()
TOOL = {"name": "run_query", "description": "Run SQL", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}
BEHAVIOR = ["is_error_mapped_to_incomplete_status", "successful_tool_result_completed", "tool_output_text_forwarded"]


def body(is_error, text):
    return {"model": "openai/gpt-4o", "max_tokens": 64, "tools": [TOOL], "messages": [
        {"role": "user", "content": "count rows"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_k7", "name": "run_query", "input": {"q": "select 1"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_k7", "is_error": is_error, "content": text}]}]}


def outputs(cap):
    return [i for i in (cap.json or {}).get("input", []) if isinstance(i, dict) and i.get("type") == "function_call_output"]


try:
    with Upstream(respond) as up, bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
        e = gw.post_json("/anthropic/v1/messages", body(True, "syntax error near FROM"))
        err = outputs(up.last("/responses"))
        ok = gw.post_json("/anthropic/v1/messages", body(False, "1 row"))
        good = outputs(up.last("/responses"))
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("is_error_mapped_to_incomplete_status"):
        assert e[0] == 200 and err, f"status {e[0]}, outputs {err}"
        assert err[0].get("status") == "incomplete", err[0]

    with R.test("successful_tool_result_completed"):
        assert ok[0] == 200 and good, f"status {ok[0]}"
        assert good[0].get("status") in (None, "completed"), good[0]

    with R.test("tool_output_text_forwarded"):
        assert "syntax error near FROM" in json.dumps(err) and "1 row" in json.dumps(good), (err, good)
        assert err[0].get("call_id") == "toolu_k7", err[0]

code, out = run("go test -count=1 ./providers/anthropic/", cwd="/work/repo/core", timeout=900)
R.check("go_unit_tests_anthropic_provider", code == 0, out)
R.write()
