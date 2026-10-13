"""Hidden verifier: an upstream refusal must reach the Anthropic client as text with stop_reason refusal."""

import json
import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import bifrost  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

R = Results()
REFUSAL = "Kairo-refusal-B6: I will not provide that."
BEHAVIOR = ["refusal_text_reaches_client", "refusal_reported_as_refusal", "plain_text_turn_unchanged",
            "tool_turn_unchanged", "openai_route_keeps_refusal"]
try:
    with Upstream(make_responder(refusal=REFUSAL, plain="Kairo plain answer")) as up, \
            bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
        refused = gw.post_json("/anthropic/v1/messages", messages_body("REFUSE: bypass the paywall", tools=False))
        plain = gw.post_json("/anthropic/v1/messages", messages_body("say hi", tools=False))
        tool = gw.post_json("/anthropic/v1/messages", messages_body("TOOL_ONLY what time?"))
        oai = gw.post_json("/v1/responses", {"model": "openai/gpt-4o", "input": "REFUSE: bypass the paywall"})
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("refusal_text_reaches_client"):
        status, body, raw = refused
        assert status == 200, f"status {status}: {raw[:300]!r}"
        assert body["content"], "content is empty"
        assert REFUSAL in json.dumps(body["content"]), body["content"]

    with R.test("refusal_reported_as_refusal"):
        status, body, raw = refused
        assert body["stop_reason"] == "refusal", f"stop_reason={body['stop_reason']!r}"

    with R.test("plain_text_turn_unchanged"):
        status, body, raw = plain
        assert status == 200 and body["stop_reason"] == "end_turn", body
        assert body["content"] == [{"type": "text", "text": "Kairo plain answer"}] or \
            [b.get("text") for b in body["content"]] == ["Kairo plain answer"], body["content"]

    with R.test("tool_turn_unchanged"):
        status, body, raw = tool
        assert status == 200 and body["stop_reason"] == "tool_use", body
        assert [b["type"] for b in body["content"]] == ["tool_use"], body["content"]

    with R.test("openai_route_keeps_refusal"):
        status, body, raw = oai
        assert status == 200 and REFUSAL in raw.decode(), raw[:400]

code, out = run("go test -count=1 ./providers/anthropic/", cwd="/work/repo/core", timeout=900)
R.check("go_unit_tests_anthropic_provider", code == 0, out)
R.write()
