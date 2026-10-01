"""Hidden verifier: a tool-only Chat reply must not gain an empty Anthropic text block."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

import anthropic_sse as sse  # noqa: E402

R = Results()
TOOLS = [{"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
BEHAVIOR = ["tool_only_turn_has_no_empty_text_block", "tool_only_turn_reports_tool_use", "text_turn_keeps_text",
            "stream_tool_turn_clean"]
try:
    with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
        tool = gw.post_json("/v1/messages", messages_body("TOOL now", tools=TOOLS))
        text = gw.post_json("/v1/messages", messages_body("hello"))
        stream = gw.post("/v1/messages", messages_body("hello", stream=True))
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("tool_only_turn_has_no_empty_text_block"):
        status, body, raw = tool
        assert status == 200, f"status {status}"
        empties = [b for b in body["content"] if b.get("type") == "text" and not b.get("text")]
        assert not empties, f"content: {body['content']}"

    with R.test("tool_only_turn_reports_tool_use"):
        status, body, raw = tool
        uses = [b for b in body["content"] if b.get("type") == "tool_use"]
        assert body["stop_reason"] == "tool_use" and len(uses) == 1 and uses[0]["name"] == "lookup", body

    with R.test("text_turn_keeps_text"):
        status, body, raw = text
        assert status == 200 and body["content"] == [{"type": "text", "text": "ok"}] or \
            [b.get("text") for b in body["content"]] == ["ok"], body

    with R.test("stream_tool_turn_clean"):
        assert stream[0] == 200 and sse.text(stream[2]) == "ok", sse.text(stream[2])

code, out = run("cargo test --locked -q -p switchyard-translation", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_translation", code == 0, out)
R.write()
