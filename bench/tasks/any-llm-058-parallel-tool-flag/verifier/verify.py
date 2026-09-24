"""Hidden verifier: disable_parallel_tool_use must reach an OpenAI-compatible backend."""

from kairo_verify import Results, run
from rigs import anyllm

R = Results()
TOOLS = [
    {"name": "lookup_city", "description": "Look up a city code",
     "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}},
    {"name": "get_time", "description": "Current time in a zone",
     "input_schema": {"type": "object", "properties": {"zone": {"type": "string"}}, "required": ["zone"]}},
]
MSGS = [{"role": "user", "content": "codes for Oslo and Lima, then the time in UTC"}]


def send(tool_choice):
    return anyllm.call(messages=MSGS, tools=TOOLS, tool_choice=tool_choice)


auto_off = send({"type": "auto", "disable_parallel_tool_use": True})
any_off = send({"type": "any", "disable_parallel_tool_use": True})
named_off = send({"type": "tool", "name": "lookup_city", "disable_parallel_tool_use": True})
auto_plain = send({"type": "auto"})
any_plain = send({"type": "any"})
named_plain = send({"type": "tool", "name": "get_time"})


def body(out):
    assert out.error is None, f"client raised {out.error!r}"
    assert out.forwarded is not None, "nothing reached the upstream"
    return out.forwarded


with R.test("disable_parallel_auto_forwarded"):
    b = body(auto_off)
    assert b.get("parallel_tool_calls") is False, f"parallel_tool_calls={b.get('parallel_tool_calls')!r}"

with R.test("disable_parallel_any_forwarded"):
    b = body(any_off)
    assert b.get("parallel_tool_calls") is False, f"parallel_tool_calls={b.get('parallel_tool_calls')!r}"
    assert b.get("tool_choice") == "required", f"tool_choice={b.get('tool_choice')!r}"

with R.test("disable_parallel_named_tool_forwarded"):
    b = body(named_off)
    assert b.get("parallel_tool_calls") is False, f"parallel_tool_calls={b.get('parallel_tool_calls')!r}"
    assert b.get("tool_choice") == {"type": "function", "function": {"name": "lookup_city"}}, b.get("tool_choice")

with R.test("parallel_flag_absent_when_not_requested"):
    for out in (auto_plain, any_plain, named_plain):
        b = body(out)
        assert b.get("parallel_tool_calls") is not False, f"flag invented: {b.get('parallel_tool_calls')!r}"

with R.test("tool_choice_mapping_preserved"):
    assert body(auto_plain).get("tool_choice") == "auto"
    assert body(any_plain).get("tool_choice") == "required"
    assert body(named_plain).get("tool_choice") == {"type": "function", "function": {"name": "get_time"}}

with R.test("tools_forwarded"):
    b = body(auto_off)
    names = [t.get("function", {}).get("name") for t in b.get("tools", [])]
    assert names == ["lookup_city", "get_time"], names
    params = b["tools"][0]["function"].get("parameters", {})
    assert params.get("required") == ["city"], params

code, out = run("python -m pytest -q -p no:cacheprovider tests/unit/test_messages_compat.py "
                "--deselect tests/unit/test_messages_compat.py::test_user_blocks_tool_result_with_list_content",
                cwd="/work/repo", timeout=600)
R.check("upstream_unit_tests_messages_compat", code == 0, out)
R.write()
