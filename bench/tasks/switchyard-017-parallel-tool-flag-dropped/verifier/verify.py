"""Hidden verifier: Anthropic disable_parallel_tool_use must become parallel_tool_calls false."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

R = Results()
TOOLS = [{"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}},
         {"name": "fetch", "description": "d", "input_schema": {"type": "object", "properties": {"u": {"type": "string"}}}}]
BEHAVIOR = ["disable_parallel_auto_forwarded", "disable_parallel_named_tool_forwarded", "parallel_flag_not_invented",
            "tool_choice_mapping_preserved", "openai_route_keeps_parallel_false"]
try:
    with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
        def send(tc):
            st, _, raw = gw.post_json("/v1/messages", messages_body("go", tools=TOOLS, tool_choice=tc))
            return st, up.last().json
        auto_off = send({"type": "auto", "disable_parallel_tool_use": True})
        named_off = send({"type": "tool", "name": "fetch", "disable_parallel_tool_use": True})
        auto = send({"type": "auto"})
        named = send({"type": "tool", "name": "lookup"})
        any_ = send({"type": "any"})
        st, _, _ = gw.post_json("/v1/chat/completions", {"model": "captured-model", "messages": [{"role": "user", "content": "x"}],
                                                        "parallel_tool_calls": False,
                                                        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]})
        oai = (st, up.last().json)
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("disable_parallel_auto_forwarded"):
        assert auto_off[0] == 200 and auto_off[1].get("parallel_tool_calls") is False, auto_off[1].get("parallel_tool_calls")

    with R.test("disable_parallel_named_tool_forwarded"):
        assert named_off[0] == 200 and named_off[1].get("parallel_tool_calls") is False, named_off[1].get("parallel_tool_calls")
        assert named_off[1].get("tool_choice") == {"type": "function", "function": {"name": "fetch"}}, named_off[1].get("tool_choice")

    with R.test("parallel_flag_not_invented"):
        for st, fwd in (auto, named, any_):
            assert st == 200 and fwd.get("parallel_tool_calls") is not False, fwd.get("parallel_tool_calls")

    with R.test("tool_choice_mapping_preserved"):
        assert auto[1].get("tool_choice") == "auto" and any_[1].get("tool_choice") == "required", (auto[1].get("tool_choice"), any_[1].get("tool_choice"))
        assert named[1].get("tool_choice") == {"type": "function", "function": {"name": "lookup"}}, named[1].get("tool_choice")

    with R.test("openai_route_keeps_parallel_false"):
        assert oai[0] == 200 and oai[1].get("parallel_tool_calls") is False, oai[1]

code, out = run("cargo test --locked -q -p switchyard-translation", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_translation", code == 0, out)
R.write()
