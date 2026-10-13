"""Hidden verifier: a streamed turn containing a tool_use block must end with stop_reason tool_use."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import bifrost  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

import anthropic_sse as sse  # noqa: E402

R = Results()
BEHAVIOR = ["stream_text_then_tool_reports_tool_use", "stream_tool_only_reports_tool_use",
            "stream_plain_text_reports_end_turn", "nonstream_text_then_tool_reports_tool_use",
            "stream_truncation_reports_max_tokens"]
try:
    with Upstream(make_responder(lead="Checking the clock for you.")) as up, \
            bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
        s_mix = gw.post("/anthropic/v1/messages", messages_body("TEXT_THEN_TOOL time in UTC?", stream=True))
        s_tool = gw.post("/anthropic/v1/messages", messages_body("TOOL_ONLY time in UTC?", stream=True))
        s_plain = gw.post("/anthropic/v1/messages", messages_body("say hi", stream=True))
        s_trunc = gw.post("/anthropic/v1/messages", messages_body("TRUNCATE list the steps", stream=True))
        n_mix = gw.post_json("/anthropic/v1/messages", messages_body("TEXT_THEN_TOOL time in UTC?"))
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("stream_text_then_tool_reports_tool_use"):
        status, _, raw = s_mix
        assert status == 200, f"status {status}"
        assert sse.block_types(raw) == ["text", "tool_use"], sse.block_types(raw)
        assert sse.stop_reason(raw) == "tool_use", f"stop_reason={sse.stop_reason(raw)!r}"

    with R.test("stream_tool_only_reports_tool_use"):
        status, _, raw = s_tool
        assert status == 200 and sse.stop_reason(raw) == "tool_use", sse.stop_reason(raw)

    with R.test("stream_plain_text_reports_end_turn"):
        status, _, raw = s_plain
        assert status == 200 and sse.stop_reason(raw) == "end_turn", sse.stop_reason(raw)
        assert sse.block_types(raw) == ["text"], sse.block_types(raw)

    with R.test("nonstream_text_then_tool_reports_tool_use"):
        status, body, raw = n_mix
        assert status == 200 and body["stop_reason"] == "tool_use", body

    with R.test("stream_truncation_reports_max_tokens"):
        status, _, raw = s_trunc
        assert status == 200 and sse.stop_reason(raw) == "max_tokens", sse.stop_reason(raw)

code, out = run("go test -count=1 ./providers/anthropic/", cwd="/work/repo/core", timeout=900)
R.check("go_unit_tests_anthropic_provider", code == 0, out)
R.write()
