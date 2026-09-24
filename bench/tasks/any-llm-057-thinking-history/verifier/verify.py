"""Hidden verifier: any-llm Messages bridge must forward replayed thinking."""

import json

from kairo_verify import Results, run
from rigs import anyllm

R = Results()
THINK_A = "Carry-probe Q7: three plus three is six."
THINK_B = "Lookup-probe Z3: I should call the city tool."


def assistant_messages(body):
    return [m for m in (body or {}).get("messages", []) if m.get("role") == "assistant"]


def visible(msg):
    content = msg.get("content")
    return content if isinstance(content, str) else json.dumps(content)


def reasoning_carrier(msg, needle):
    """True if the thinking text rides on the assistant message outside visible content."""
    rest = {k: v for k, v in msg.items() if k not in ("content", "role", "tool_calls")}
    return needle in json.dumps(rest)


history = [
    {"role": "user", "content": "3+3?"},
    {"role": "assistant", "content": [
        {"type": "thinking", "thinking": THINK_A, "signature": "sig-q7"},
        {"type": "text", "text": "6"},
    ]},
    {"role": "user", "content": "and 4+4?"},
]

tool = {"name": "lookup_city", "description": "Look up a city code",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}
tool_history = [
    {"role": "user", "content": "code for Paris?"},
    {"role": "assistant", "content": [
        {"type": "thinking", "thinking": THINK_B, "signature": "sig-z3"},
        {"type": "tool_use", "id": "toolu_k1", "name": "lookup_city", "input": {"city": "Paris"}},
    ]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_k1", "content": "PAR"}]},
]

a = anyllm.call(messages=history)
b = anyllm.call(messages=tool_history, tools=[tool])
plain = anyllm.call(messages=[{"role": "user", "content": "hello"}], system="be brief")

with R.test("thinking_block_forwarded"):
    assert a.error is None, f"client raised {a.error!r}"
    msgs = assistant_messages(a.forwarded)
    assert msgs, "no assistant message forwarded"
    assert reasoning_carrier(msgs[0], THINK_A), f"thinking text not on the assistant message: {msgs[0]}"

with R.test("thinking_forwarded_with_tool_use_turn"):
    assert b.error is None, f"client raised {b.error!r}"
    msgs = [m for m in assistant_messages(b.forwarded) if m.get("tool_calls")]
    assert msgs, f"no assistant tool_calls message forwarded: {b.forwarded}"
    assert reasoning_carrier(msgs[0], THINK_B), f"thinking text not on the tool-calling turn: {msgs[0]}"
    assert msgs[0]["tool_calls"][0]["id"] == "toolu_k1", "tool call id changed"

with R.test("visible_assistant_text_preserved"):
    assert a.error is None, f"client raised {a.error!r}"
    msgs = assistant_messages(a.forwarded)
    assert msgs and "6" in visible(msgs[0]), f"visible text lost: {msgs}"

with R.test("thinking_not_leaked_into_visible_content"):
    for out in (a, b):
        assert out.error is None, f"client raised {out.error!r}"
        for m in (out.forwarded or {}).get("messages", []):
            assert THINK_A not in visible(m) and THINK_B not in visible(m), f"thinking leaked into content: {m}"

with R.test("plain_conversation_unchanged"):
    assert plain.error is None, f"client raised {plain.error!r}"
    msgs = plain.forwarded["messages"]
    assert msgs[0] == {"role": "system", "content": "be brief"}, msgs
    assert msgs[-1] == {"role": "user", "content": "hello"}, msgs
    assert plain.response.content[0].text == "ok"

# Upstream unit tests for the bridge, minus the one that asserts the old behavior the fix
# PR changed (an image block without a source was silently ignored).
code, out = run("python -m pytest -q -p no:cacheprovider tests/unit/test_messages_compat.py "
                "--deselect tests/unit/test_messages_compat.py::test_user_blocks_tool_result_with_list_content",
                cwd="/work/repo", timeout=600)
R.check("upstream_unit_tests_messages_compat", code == 0, out)
R.write()
