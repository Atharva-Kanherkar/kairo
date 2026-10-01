"""Hidden verifier: a tool-result image must reach an OpenAI Chat backend as an image, in a valid request."""

import json
import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder  # noqa: E402

R = Results()
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC"
TOOLS = [{"name": "screenshot", "description": "Capture", "input_schema": {"type": "object", "properties": {}}}]
BEHAVIOR = ["tool_result_image_forwarded_as_image", "tool_result_image_not_dumped_into_text",
            "tool_messages_text_only_and_adjacent", "tool_result_text_preserved", "user_image_still_forwarded"]
img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}


def image_parts(msgs):
    return [p for m in msgs if m.get("role") == "user" and isinstance(m.get("content"), list)
            for p in m["content"] if isinstance(p, dict) and p.get("type") == "image_url"]


try:
    with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
        s1, b1, r1 = gw.post_json("/v1/messages", {"model": "captured-model", "max_tokens": 64, "tools": TOOLS, "messages": [
            {"role": "user", "content": "describe the screen"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_s1", "name": "screenshot", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_s1",
                                          "content": [{"type": "text", "text": "capture-S1"}, img]}]}]})
        fwd = up.last().json
        s2, b2, r2 = gw.post_json("/v1/messages", {"model": "captured-model", "max_tokens": 64,
                                                   "messages": [{"role": "user", "content": [{"type": "text", "text": "what is this"}, img]}]})
        fwd_user = up.last().json
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    msgs = fwd.get("messages", []) if fwd else []

    with R.test("tool_result_image_forwarded_as_image"):
        assert s1 == 200, f"status {s1}: {r1[:300]!r}"
        urls = [p.get("image_url", {}).get("url", "") for p in image_parts(msgs)]
        assert any(PNG in u for u in urls), f"no image_url part carries the PNG: {json.dumps(msgs)[:600]}"

    with R.test("tool_result_image_not_dumped_into_text"):
        tool_text = json.dumps([m.get("content") for m in msgs if m.get("role") == "tool"])
        assert PNG not in tool_text, "the image was serialized into the tool message text"

    with R.test("tool_messages_text_only_and_adjacent"):
        idx = next(i for i, m in enumerate(msgs) if m.get("role") == "assistant" and m.get("tool_calls"))
        assert msgs[idx + 1].get("role") == "tool" and msgs[idx + 1].get("tool_call_id") == "toolu_s1", msgs[idx + 1:]
        for m in msgs:
            if m.get("role") == "tool":
                c = m.get("content")
                assert isinstance(c, str) or all(p.get("type") == "text" for p in c), f"non-text tool content: {c}"

    with R.test("tool_result_text_preserved"):
        assert "capture-S1" in json.dumps([m.get("content") for m in msgs if m.get("role") == "tool"]), msgs

    with R.test("user_image_still_forwarded"):
        assert s2 == 200 and any(PNG in p.get("image_url", {}).get("url", "") for p in image_parts(fwd_user["messages"])), fwd_user

code, out = run("cargo test --locked -q -p switchyard-translation", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_translation", code == 0, out)
R.write()
