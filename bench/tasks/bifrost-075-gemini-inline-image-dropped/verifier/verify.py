"""Hidden verifier: generated inline media must survive Gemini -> Chat Completions translation."""

import json
import sys

from kairo_verify import Results, Upstream, sse_events

sys.path.insert(0, "/work/kairo")
from rigs import bifrost  # noqa: E402
from upstream import MODEL, chat_body, gateway_config, make_responder  # noqa: E402

R = Results()
# A different 1x1 PNG and caption than the public reproducer uses.
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC"
CAPTION = "Kairo caption G5: a green pixel."
BEHAVIOR = ["chat_unary_keeps_generated_image", "chat_stream_keeps_generated_image", "chat_unary_keeps_caption",
            "chat_stream_keeps_caption", "responses_route_keeps_generated_image", "text_only_turn_unchanged"]

try:
    with Upstream(make_responder(caption=CAPTION, image=PNG)) as up, bifrost.Gateway(gateway_config(up.url)) as gw:
        unary = gw.post_json("/v1/chat/completions", chat_body("Draw a green pixel."))
        stream = gw.post("/v1/chat/completions", chat_body("Draw a green pixel.", stream=True))
        responses = gw.post_json("/v1/responses", {"model": MODEL, "input": "Draw a green pixel."})
        text_only = gw.post_json("/v1/chat/completions", chat_body("TEXT ONLY please"))
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    def message(resp):
        status, body, raw = resp
        assert status == 200, f"status {status}: {raw[:300]!r}"
        return body["choices"][0]["message"]

    stream_text = json.dumps([e["json"] for e in sse_events(stream[2]) if e["json"]])

    with R.test("chat_unary_keeps_generated_image"):
        assert PNG in json.dumps(message(unary)), f"image bytes missing from the message: {json.dumps(message(unary))[:500]}"

    with R.test("chat_stream_keeps_generated_image"):
        assert stream[0] == 200, f"status {stream[0]}"
        assert PNG in stream_text, "image bytes missing from the streamed deltas"

    with R.test("chat_unary_keeps_caption"):
        assert CAPTION in json.dumps(message(unary)), json.dumps(message(unary))[:500]

    with R.test("chat_stream_keeps_caption"):
        assert CAPTION in stream_text, stream_text[:500]

    with R.test("responses_route_keeps_generated_image"):
        status, body, raw = responses
        assert status == 200 and PNG in raw.decode(), f"status {status}"

    with R.test("text_only_turn_unchanged"):
        m = message(text_only)
        assert m.get("content") == "plain answer" or "plain answer" in json.dumps(m.get("content")), m

# The upstream fix changes ToBifrostChatCompletionStream's signature, so the base commit's
# Go unit tests for this package do not compile against a correct fix; they are not run.
R.write()
