"""Hidden verifier: an incomplete upstream turn must not be reported as end_turn, and both modes agree."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import bifrost  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

import anthropic_sse as sse  # noqa: E402

R = Results()
BEHAVIOR = ["nonstream_truncation_reports_max_tokens", "nonstream_content_filter_matches_stream",
            "stream_truncation_reports_max_tokens", "nonstream_completed_turn_reports_end_turn",
            "nonstream_tool_turn_reports_tool_use"]
try:
    with Upstream(make_responder(partial="Step one: back up the", blocked="Sorry")) as up, \
            bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
        n_trunc = gw.post_json("/anthropic/v1/messages", messages_body("TRUNCATE the migration steps", tools=False))
        s_trunc = gw.post("/anthropic/v1/messages", messages_body("TRUNCATE the migration steps", stream=True, tools=False))
        n_filter = gw.post_json("/anthropic/v1/messages", messages_body("FILTER explain", tools=False))
        s_filter = gw.post("/anthropic/v1/messages", messages_body("FILTER explain", stream=True, tools=False))
        n_plain = gw.post_json("/anthropic/v1/messages", messages_body("say hi", tools=False))
        n_tool = gw.post_json("/anthropic/v1/messages", messages_body("TOOL_ONLY time?"))
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("nonstream_truncation_reports_max_tokens"):
        status, body, raw = n_trunc
        assert status == 200, f"status {status}"
        assert body["stop_reason"] == "max_tokens", f"stop_reason={body['stop_reason']!r}"
        assert "Step one: back up the" in str(body["content"]), body["content"]

    with R.test("nonstream_content_filter_matches_stream"):
        status, body, raw = n_filter
        streamed = sse.stop_reason(s_filter[2])
        assert status == 200 and streamed is not None, f"status {status}, streamed {streamed!r}"
        assert body["stop_reason"] != "end_turn", "a content-filtered turn was reported as end_turn"
        assert body["stop_reason"] == streamed, f"non-stream {body['stop_reason']!r} vs stream {streamed!r}"

    with R.test("stream_truncation_reports_max_tokens"):
        assert sse.stop_reason(s_trunc[2]) == "max_tokens", sse.stop_reason(s_trunc[2])

    with R.test("nonstream_completed_turn_reports_end_turn"):
        status, body, raw = n_plain
        assert status == 200 and body["stop_reason"] == "end_turn", body

    with R.test("nonstream_tool_turn_reports_tool_use"):
        status, body, raw = n_tool
        assert status == 200 and body["stop_reason"] == "tool_use", body

code, out = run("go test -count=1 ./providers/anthropic/", cwd="/work/repo/core", timeout=900)
R.check("go_unit_tests_anthropic_provider", code == 0, out)
R.write()
