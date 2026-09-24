"""Hidden verifier: Anthropic output_format must become the backend's response_format."""

import json
import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

R = Results()
SCHEMA = {"type": "object", "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}},
          "required": ["sku", "qty"], "additionalProperties": False}
BEHAVIOR = ["output_format_schema_forwarded", "no_response_format_invented", "openai_route_keeps_response_format",
            "messages_forwarded"]
try:
    with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
        s1, _, r1 = gw.post_json("/v1/messages", messages_body("order two A1", output_format={"type": "json_schema", "schema": SCHEMA}))
        f1 = up.last().json
        s2, _, _ = gw.post_json("/v1/messages", messages_body("hello"))
        f2 = up.last().json
        s3, _, _ = gw.post_json("/v1/chat/completions", {"model": "captured-model", "messages": [{"role": "user", "content": "x"}],
                                                        "response_format": {"type": "json_schema", "json_schema": {"name": "o", "schema": SCHEMA}}})
        f3 = up.last().json
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("output_format_schema_forwarded"):
        assert s1 == 200, f"status {s1}: {r1[:300]!r}"
        rf = f1.get("response_format") or {}
        assert rf.get("type") == "json_schema", f"response_format={rf!r}"
        schema = (rf.get("json_schema") or {}).get("schema") or {}
        assert schema.get("required") == ["sku", "qty"] and "qty" in schema.get("properties", {}), json.dumps(rf)[:400]

    with R.test("no_response_format_invented"):
        assert s2 == 200 and "response_format" not in f2, f2.get("response_format")

    with R.test("openai_route_keeps_response_format"):
        assert s3 == 200 and (f3.get("response_format") or {}).get("type") == "json_schema", f3.get("response_format")

    with R.test("messages_forwarded"):
        assert f1["messages"][-1]["content"] in ("order two A1", [{"type": "text", "text": "order two A1"}]), f1["messages"]

code, out = run("cargo test --locked -q -p switchyard-translation", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_translation", code == 0, out)
R.write()
