"""Hidden verifier: reasoning in a mixed chunk must be emitted before the visible text."""

import sys

from kairo_verify import Results, Upstream, run, sse_events

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

import anthropic_sse as sse  # noqa: E402

R = Results()
REASON, ANSWER = "Kairo reasoning R8.", "Kairo answer A8."
BEHAVIOR = ["mixed_chunk_thinking_before_text", "mixed_chunk_keeps_both_texts", "plain_stream_unchanged",
            "buffered_reasoning_before_text"]


def thinking_text(raw):
    out = []
    for e in sse_events(raw):
        j = e["json"] or {}
        d = j.get("delta") or {}
        if j.get("type") == "content_block_delta" and d.get("type") == "thinking_delta":
            out.append(d.get("thinking", ""))
    return "".join(out)


try:
    with Upstream(make_responder(reasoning=REASON, answer=ANSWER)) as up, \
            switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
        mixed = gw.post("/v1/messages", messages_body("MIXEDCHUNK 2+2?", stream=True))
        plain = gw.post("/v1/messages", messages_body("hello", stream=True))
        buffered = gw.post_json("/v1/messages", messages_body("MIXEDCHUNK 2+2?"))
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("mixed_chunk_thinking_before_text"):
        types = [t for t in sse.block_types(mixed[2]) if t in ("thinking", "text")]
        assert types[:2] == ["thinking", "text"], f"content blocks in order: {types}"

    with R.test("mixed_chunk_keeps_both_texts"):
        assert thinking_text(mixed[2]) == REASON and sse.text(mixed[2]) == ANSWER, (thinking_text(mixed[2]), sse.text(mixed[2]))

    with R.test("plain_stream_unchanged"):
        assert sse.block_types(plain[2]) == ["text"] and sse.text(plain[2]) == "ok", sse.block_types(plain[2])

    with R.test("buffered_reasoning_before_text"):
        status, body, raw = buffered
        types = [b.get("type") for b in body.get("content", [])]
        assert status == 200 and "text" in types, f"status {status}: {types}"
        if "thinking" in types:
            assert types.index("thinking") < types.index("text"), types

code, out = run("cargo test --locked -q -p switchyard-translation", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_translation", code == 0, out)
R.write()
