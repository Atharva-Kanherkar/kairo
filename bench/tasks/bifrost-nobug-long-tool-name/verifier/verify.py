"""Hidden verifier (no-bug task): long tool names already produce a clean response, never a 5xx crash."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import bifrost  # noqa: E402
from upstream import respond  # noqa: E402

R = Results()
BEHAVIOR = ["long_tool_name_no_server_error", "long_tool_name_no_stack_trace", "normal_tool_request_succeeds"]


def body(name):
    return {"model": "openai/gpt-4o", "max_tokens": 64, "tool_choice": {"type": "auto"},
            "messages": [{"role": "user", "content": "go"}],
            "tools": [{"name": name, "description": "t", "input_schema": {"type": "object", "properties": {}}}]}


try:
    with Upstream(respond) as up, bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
        long_results = [gw.post_json("/anthropic/v1/messages", body(n)) for n in ("t" * 65, "k" * 128, "mcp__" + "z" * 300)]
        normal = gw.post_json("/anthropic/v1/messages", body("get_time"))
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("long_tool_name_no_server_error"):
        assert all(s < 500 for s, _, _ in long_results), [s for s, _, _ in long_results]

    with R.test("long_tool_name_no_stack_trace"):
        for s, _, raw in long_results:
            text = raw.decode("utf-8", "replace").lower()
            assert "panic" not in text and "goroutine" not in text and "index out of range" not in text, text[:300]

    with R.test("normal_tool_request_succeeds"):
        assert normal[0] == 200 and normal[1]["content"], normal[:2]

code, out = run("go test -count=1 ./providers/anthropic/", cwd="/work/repo/core", timeout=900)
R.check("go_unit_tests_anthropic_provider", code == 0, out)
R.write()
