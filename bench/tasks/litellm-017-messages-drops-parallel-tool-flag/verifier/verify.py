"""Hidden verifier: Anthropic disable_parallel_tool_use must reach the OpenAI backend."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import litellm  # noqa: E402
from upstream import proxy_config, respond  # noqa: E402

R = Results()
TOOLS = [{"name": "lookup_city", "description": "Look up a city code",
          "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}},
         {"name": "get_time", "description": "Time in a zone",
          "input_schema": {"type": "object", "properties": {"zone": {"type": "string"}}, "required": ["zone"]}}]
BEHAVIOR = ["disable_parallel_auto_forwarded", "disable_parallel_any_forwarded", "disable_parallel_named_tool_forwarded",
            "parallel_flag_not_invented", "tool_choice_mapping_preserved", "chat_route_keeps_parallel_false"]


def send(proxy, up, tool_choice):
    up.reset()
    status, body, raw = proxy.post_json("/v1/messages", {
        "model": "mock", "max_tokens": 64, "messages": [{"role": "user", "content": "codes for Oslo and Lima"}],
        "tools": TOOLS, "tool_choice": tool_choice})
    cap = up.last()
    assert status == 200, f"status {status}: {raw[:300]!r}"
    assert cap is not None and cap.json is not None, "nothing reached the upstream"
    return cap.json


try:
    with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
        results = {}
        for name, tc in {"auto_off": {"type": "auto", "disable_parallel_tool_use": True},
                         "any_off": {"type": "any", "disable_parallel_tool_use": True},
                         "named_off": {"type": "tool", "name": "get_time", "disable_parallel_tool_use": True},
                         "auto": {"type": "auto"}, "any": {"type": "any"},
                         "named": {"type": "tool", "name": "lookup_city"},
                         "auto_explicit_on": {"type": "auto", "disable_parallel_tool_use": False}}.items():
            try:
                results[name] = send(proxy, up, tc)
            except AssertionError as exc:
                results[name] = exc
        up.reset()
        chat = proxy.post_json("/v1/chat/completions", {
            "model": "mock", "messages": [{"role": "user", "content": "hi"}], "parallel_tool_calls": False,
            "tools": [{"type": "function", "function": {"name": "lookup_city", "parameters": TOOLS[0]["input_schema"]}}]})
        chat_cap = up.last("/chat/completions")
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    def body(name):
        b = results[name]
        if isinstance(b, AssertionError):
            raise b
        return b

    with R.test("disable_parallel_auto_forwarded"):
        b = body("auto_off")
        assert b.get("parallel_tool_calls") is False, f"parallel_tool_calls={b.get('parallel_tool_calls')!r}"

    with R.test("disable_parallel_any_forwarded"):
        b = body("any_off")
        assert b.get("parallel_tool_calls") is False, f"parallel_tool_calls={b.get('parallel_tool_calls')!r}"
        assert b.get("tool_choice") == "required", b.get("tool_choice")

    with R.test("disable_parallel_named_tool_forwarded"):
        b = body("named_off")
        assert b.get("parallel_tool_calls") is False, f"parallel_tool_calls={b.get('parallel_tool_calls')!r}"
        assert b.get("tool_choice") == {"type": "function", "name": "get_time"}, b.get("tool_choice")

    with R.test("parallel_flag_not_invented"):
        for name in ("auto", "any", "named", "auto_explicit_on"):
            assert body(name).get("parallel_tool_calls") is not False, f"{name}: flag invented"

    with R.test("tool_choice_mapping_preserved"):
        assert body("auto").get("tool_choice") == "auto"
        assert body("any").get("tool_choice") == "required"
        assert body("named").get("tool_choice") == {"type": "function", "name": "lookup_city"}
        assert [t.get("name") for t in body("auto").get("tools", [])] == ["lookup_city", "get_time"]

    with R.test("chat_route_keeps_parallel_false"):
        assert chat[0] == 200 and chat_cap is not None, f"chat status {chat[0]}"
        assert chat_cap.json.get("parallel_tool_calls") is False, chat_cap.json

D = "tests/test_litellm/llms/anthropic/experimental_pass_through/responses_adapters"
code, out = run(f"python -m pytest -q -p no:cacheprovider {D}", cwd="/work/repo", timeout=900)
R.check("upstream_unit_tests_responses_adapters", code == 0, out)
R.write()
