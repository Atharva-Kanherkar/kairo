"""Hidden verifier (no-bug task): disable_parallel_tool_use already maps to parallel_tool_calls false."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import gomodel  # noqa: E402
from upstream import messages_body, respond  # noqa: E402

R = Results()
TOOLS = [{"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}]
BEHAVIOR = ["disable_parallel_maps_to_parallel_false", "flag_absent_not_invented", "tool_choice_mapping_intact"]
try:
    with Upstream(respond) as up, gomodel.Gateway(up.url + "/v1") as gw:
        def send(tc):
            up.reset()
            st, _, raw = gw.post_json("/v1/messages", messages_body("go", tools=TOOLS, tool_choice=tc))
            return st, up.last("/chat/completions").json
        off = {k: send({"type": k, "disable_parallel_tool_use": True, **({"name": "lookup"} if k == "tool" else {})})
               for k in ("auto", "any", "tool")}
        on = {k: send({"type": k, **({"name": "lookup"} if k == "tool" else {})}) for k in ("auto", "any", "tool")}
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("disable_parallel_maps_to_parallel_false"):
        for k, (st, fwd) in off.items():
            assert st == 200 and fwd.get("parallel_tool_calls") is False, (k, fwd.get("parallel_tool_calls"))

    with R.test("flag_absent_not_invented"):
        for k, (st, fwd) in on.items():
            assert st == 200 and fwd.get("parallel_tool_calls") is not False, (k, fwd.get("parallel_tool_calls"))

    with R.test("tool_choice_mapping_intact"):
        assert on["auto"][1].get("tool_choice") == "auto" and on["any"][1].get("tool_choice") == "required"
        assert on["tool"][1].get("tool_choice") == {"type": "function", "function": {"name": "lookup"}}, on["tool"][1]

code, out = run("go test -count=1 ./internal/anthropicapi/", cwd="/work/repo", timeout=900)
R.check("go_unit_tests_anthropicapi", code == 0, out)
R.write()
