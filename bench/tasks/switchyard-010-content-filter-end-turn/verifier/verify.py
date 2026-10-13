"""Hidden verifier: an upstream content_filter stop must not become a normal end_turn."""

import sys

from kairo_verify import Results, Upstream

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

import anthropic_sse as sse  # noqa: E402

R = Results()
BEHAVIOR = ["content_filter_not_end_turn_buffered", "content_filter_not_end_turn_stream", "normal_stop_is_end_turn",
            "tool_call_is_tool_use", "partial_text_still_delivered", "openai_route_keeps_content_filter"]
SAFETY = {"refusal"}
try:
    with Upstream(make_responder(filtered="Partial K3")) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
        filt = gw.post_json("/v1/messages", messages_body("FILTER this"))
        filt_stream = gw.post("/v1/messages", messages_body("FILTER this", stream=True))
        plain = gw.post_json("/v1/messages", messages_body("hello"))
        tool = gw.post_json("/v1/messages", messages_body("TOOL now", tools=[{"name": "lookup", "description": "d",
                                                                              "input_schema": {"type": "object", "properties": {}}}]))
        oai = gw.post_json("/v1/chat/completions", {"model": "captured-model", "messages": [{"role": "user", "content": "FILTER"}]})
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("content_filter_not_end_turn_buffered"):
        status, body, raw = filt
        assert status == 200, f"status {status}: {raw[:300]!r}"
        assert body["stop_reason"] in SAFETY, f"stop_reason={body['stop_reason']!r}"

    with R.test("content_filter_not_end_turn_stream"):
        assert filt_stream[0] == 200, f"status {filt_stream[0]}"
        assert sse.stop_reason(filt_stream[2]) in SAFETY, f"stop_reason={sse.stop_reason(filt_stream[2])!r}"

    with R.test("normal_stop_is_end_turn"):
        assert plain[0] == 200 and plain[1]["stop_reason"] == "end_turn", plain[1]

    with R.test("tool_call_is_tool_use"):
        assert tool[0] == 200 and tool[1]["stop_reason"] == "tool_use", tool[1]

    with R.test("partial_text_still_delivered"):
        assert "Partial K3" in str(filt[1]["content"]) and "Partial K3" in sse.text(filt_stream[2]), (filt[1], sse.text(filt_stream[2]))

    with R.test("openai_route_keeps_content_filter"):
        assert oai[0] == 200 and oai[1]["choices"][0]["finish_reason"] == "content_filter", oai[1]
R.write()
