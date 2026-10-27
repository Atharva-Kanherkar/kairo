"""Hidden verifier (no-bug task): an empty Gemini candidate list is already handled cleanly."""

import sys

from kairo_verify import Results, Upstream

sys.path.insert(0, "/work/kairo")
from rigs import litellm  # noqa: E402
from upstream import proxy_config, respond  # noqa: E402

R = Results()
BEHAVIOR = ["empty_candidates_no_server_error", "empty_candidates_with_long_tool_name_no_server_error",
            "blocked_turn_not_reported_as_success_text", "normal_turn_unchanged"]


def body(prompt, tools=None):
    b = {"model": "gem", "max_tokens": 32, "messages": [{"role": "user", "content": prompt}]}
    if tools:
        b["tools"], b["tool_choice"] = tools, {"type": "any"}
    return b


try:
    with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
        empty = proxy.post_json("/v1/messages", body("EMPTY blocked prompt"))
        long_tool = proxy.post_json("/v1/messages", body("EMPTY go", [{"name": "mcp__" + "t" * 80, "description": "d",
                                                                       "input_schema": {"type": "object", "properties": {}}}]))
        normal = proxy.post_json("/v1/messages", body("hello"))
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    for name, resp in (("empty_candidates_no_server_error", empty),
                       ("empty_candidates_with_long_tool_name_no_server_error", long_tool)):
        with R.test(name):
            status, parsed, raw = resp
            text = raw.decode("utf-8", "replace")
            assert status < 500, f"status {status}: {text[:300]}"
            assert "index out of range" not in text and "Traceback" not in text, text[:300]

    with R.test("blocked_turn_not_reported_as_success_text"):
        status, parsed, raw = empty
        assert parsed is not None and parsed.get("type") in ("message", "error"), raw[:300]
        if parsed.get("type") == "message":
            assert parsed.get("stop_reason") != "end_turn" or not parsed.get("content"), parsed

    with R.test("normal_turn_unchanged"):
        status, parsed, raw = normal
        assert status == 200 and parsed["content"][0]["text"] == "gemini ok" and parsed["stop_reason"] == "end_turn", parsed
R.write()
