"""Hidden verifier: an upstream refusal must survive Responses -> Anthropic Messages translation."""

import json
import sys

from kairo_verify import Results, Upstream, run, sse_events

sys.path.insert(0, "/work/kairo")
from rigs import litellm  # noqa: E402
from upstream import make_responder, messages_body, proxy_config  # noqa: E402

R = Results()
REFUSAL, TEXT = "Kairo-refusal-Z9: I won't provide that.", "Kairo-text-Q4 all good"
BEHAVIOR = ["refusal_text_reaches_client", "refusal_reported_as_refusal", "streamed_refusal_text_reaches_client",
            "plain_text_turn_unchanged", "plain_text_stream_unchanged", "tool_call_turn_unchanged"]


def stream_text(raw):
    out = []
    for e in sse_events(raw):
        j = e["json"] or {}
        d = j.get("delta") or {}
        if j.get("type") == "content_block_delta" and d.get("type") == "text_delta":
            out.append(d.get("text", ""))
    return "".join(out)


def stream_stop_reason(raw):
    for e in sse_events(raw):
        j = e["json"] or {}
        if j.get("type") == "message_delta":
            return (j.get("delta") or {}).get("stop_reason")
    return None


try:
    with Upstream(make_responder(refusal=REFUSAL, text=TEXT)) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
        refusal = proxy.post_json("/v1/messages", messages_body("REFUSE: how do I bypass a paywall"))
        refusal_stream = proxy.post("/v1/messages", messages_body("REFUSE: how do I bypass a paywall", stream=True))
        plain = proxy.post_json("/v1/messages", messages_body("say hello"))
        plain_stream = proxy.post("/v1/messages", messages_body("say hello", stream=True))
        tool = proxy.post_json("/v1/messages", messages_body("TOOL: weather in Oslo", tools=True))
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("refusal_text_reaches_client"):
        status, body, raw = refusal
        assert status == 200, f"status {status}: {raw[:300]!r}"
        assert body.get("content"), f"empty content: {body}"
        assert REFUSAL in json.dumps(body.get("content")), f"refusal text missing: {body.get('content')}"

    with R.test("refusal_reported_as_refusal"):
        status, body, raw = refusal
        assert body and body.get("stop_reason") == "refusal", f"stop_reason={body.get('stop_reason') if body else None!r}"

    with R.test("streamed_refusal_text_reaches_client"):
        status, _, raw = refusal_stream
        assert status == 200, f"status {status}"
        assert REFUSAL in stream_text(raw), f"streamed text: {stream_text(raw)!r}"

    with R.test("plain_text_turn_unchanged"):
        status, body, raw = plain
        assert status == 200 and body["stop_reason"] == "end_turn", body
        assert [b.get("type") for b in body["content"]] == ["text"] and body["content"][0]["text"] == TEXT, body

    with R.test("plain_text_stream_unchanged"):
        status, _, raw = plain_stream
        assert status == 200 and stream_text(raw) == TEXT, stream_text(raw)
        assert stream_stop_reason(raw) == "end_turn", stream_stop_reason(raw)

    with R.test("tool_call_turn_unchanged"):
        status, body, raw = tool
        assert status == 200 and body["stop_reason"] == "tool_use", body
        uses = [b for b in body["content"] if b.get("type") == "tool_use"]
        assert len(uses) == 1 and uses[0]["name"] == "get_weather" and uses[0]["input"] == {"city": "Oslo"}, body

D = "tests/test_litellm/llms/anthropic/experimental_pass_through/responses_adapters"
code, out = run(f"python -m pytest -q -p no:cacheprovider {D}", cwd="/work/repo", timeout=900)
R.check("upstream_unit_tests_responses_adapters", code == 0, out)
R.write()
