"""Hidden verifier: image bytes inside an Anthropic tool_result must reach the backend,
on a request that is still valid OpenAI Chat Completions."""

import json

from kairo_verify import Results, run
from rigs import anyllm

R = Results()
# A different 1x1 PNG than the public reproducer uses.
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC"
TOOLS = [
    {"name": "screenshot", "description": "Capture the screen", "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_title", "description": "Read the page title", "input_schema": {"type": "object", "properties": {}}},
]


def img(data=PNG):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


single = anyllm.call(tools=TOOLS, messages=[
    {"role": "user", "content": "describe the screen"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_s1", "name": "screenshot", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_s1",
                                  "content": [{"type": "text", "text": "capture-7 attached"}, img()]}]},
])
parallel = anyllm.call(tools=TOOLS, messages=[
    {"role": "user", "content": "title and screen please"},
    {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_p1", "name": "read_title", "input": {}},
        {"type": "tool_use", "id": "toolu_p2", "name": "screenshot", "input": {}},
    ]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_p1", "content": "Kairo Dashboard"},
        {"type": "tool_result", "tool_use_id": "toolu_p2", "content": [{"type": "text", "text": "capture-8"}, img()]},
    ]},
])
user_img = anyllm.call(messages=[{"role": "user", "content": [{"type": "text", "text": "what is this"}, img()]}])


def body(out):
    assert out.error is None, f"client raised {out.error!r}"
    assert out.forwarded is not None, "nothing reached the upstream"
    return out.forwarded


def image_urls(msg):
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [p.get("image_url", {}).get("url", "") for p in content if isinstance(p, dict) and p.get("type") == "image_url"]


def forwarded_image(b):
    return any(PNG in url for m in b["messages"] if m.get("role") == "user" for url in image_urls(m))


def check_tool_rules(b, expected_ids):
    """OpenAI rules: role=tool messages carry text only and directly follow the assistant tool_calls turn."""
    msgs = b["messages"]
    idx = next(i for i, m in enumerate(msgs) if m.get("role") == "assistant" and m.get("tool_calls"))
    ids = [c["id"] for c in msgs[idx]["tool_calls"]]
    following = msgs[idx + 1: idx + 1 + len(ids)]
    assert [m.get("role") for m in following] == ["tool"] * len(ids), \
        f"tool messages must directly follow the tool_calls turn: {[m.get('role') for m in msgs[idx + 1:]]}"
    assert [m.get("tool_call_id") for m in following] == expected_ids, [m.get("tool_call_id") for m in following]
    for m in msgs:
        if m.get("role") != "tool":
            continue
        c = m.get("content")
        if isinstance(c, list):
            assert all(isinstance(p, dict) and p.get("type") == "text" for p in c), f"non-text part on tool message: {c}"
        else:
            assert isinstance(c, str), f"tool content must be text: {c!r}"


with R.test("tool_result_image_forwarded"):
    b = body(single)
    assert forwarded_image(b), "PNG bytes from the tool result are not in any image_url part: " + json.dumps(b["messages"])[:800]

with R.test("image_forwarded_from_parallel_tool_results"):
    b = body(parallel)
    assert forwarded_image(b), "PNG bytes from the second tool result are missing"
    check_tool_rules(b, ["toolu_p1", "toolu_p2"])

with R.test("tool_messages_follow_openai_rules"):
    check_tool_rules(body(single), ["toolu_s1"])
    check_tool_rules(body(parallel), ["toolu_p1", "toolu_p2"])

with R.test("tool_result_text_preserved"):
    tool_msgs = [m for m in body(single)["messages"] if m.get("role") == "tool"]
    assert tool_msgs and "capture-7 attached" in json.dumps(tool_msgs[0].get("content")), tool_msgs
    tool_msgs = [m for m in body(parallel)["messages"] if m.get("role") == "tool"]
    assert "Kairo Dashboard" in json.dumps(tool_msgs[0].get("content")), tool_msgs

with R.test("user_image_still_forwarded"):
    assert forwarded_image(body(user_img)), "a plain user image no longer reaches the backend"

code, out = run("python -m pytest -q -p no:cacheprovider tests/unit/test_messages_compat.py "
                "--deselect tests/unit/test_messages_compat.py::test_user_blocks_tool_result_with_list_content",
                cwd="/work/repo", timeout=600)
R.check("upstream_unit_tests_messages_compat", code == 0, out)
R.write()
