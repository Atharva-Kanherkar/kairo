"""Hidden verifier: Anthropic tool strictness must map to OpenAI function.strict."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

R = Results()
SCHEMA = {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"], "additionalProperties": False}
BEHAVIOR = ["strict_true_forwarded", "strict_absent_not_invented", "tool_schema_forwarded", "openai_route_keeps_strict"]
try:
    with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
        s1, _, r1 = gw.post_json("/v1/messages", messages_body("order", tools=[{"name": "order", "description": "o", "strict": True, "input_schema": SCHEMA}]))
        t1 = up.last().json.get("tools", [])
        s2, _, _ = gw.post_json("/v1/messages", messages_body("order", tools=[{"name": "order", "description": "o", "input_schema": SCHEMA}]))
        t2 = up.last().json.get("tools", [])
        s3, _, _ = gw.post_json("/v1/chat/completions", {"model": "captured-model", "messages": [{"role": "user", "content": "x"}],
                                                        "tools": [{"type": "function", "function": {"name": "order", "strict": True, "parameters": SCHEMA}}]})
        t3 = up.last().json.get("tools", [])
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("strict_true_forwarded"):
        assert s1 == 200 and t1, f"status {s1}: {r1[:200]!r}"
        assert t1[0]["function"].get("strict") is True, t1[0]

    with R.test("strict_absent_not_invented"):
        assert s2 == 200 and t2[0]["function"].get("strict") is not True, t2[0]

    with R.test("tool_schema_forwarded"):
        assert t1[0]["function"]["name"] == "order" and t1[0]["function"]["parameters"].get("required") == ["sku"], t1[0]

    with R.test("openai_route_keeps_strict"):
        assert s3 == 200 and t3[0]["function"].get("strict") is True, t3

code, out = run("cargo test --locked -q -p switchyard-translation", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_translation", code == 0, out)
R.write()
