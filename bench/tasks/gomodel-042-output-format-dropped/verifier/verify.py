"""Hidden verifier: Anthropic structured output must reach the backend as response_format."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import gomodel  # noqa: E402
from upstream import messages_body, respond  # noqa: E402

R = Results()
SCHEMA = {"type": "object", "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}},
          "required": ["sku", "qty"], "additionalProperties": False}
BEHAVIOR = ["output_config_schema_forwarded", "output_format_schema_forwarded", "no_response_format_invented",
            "openai_route_keeps_response_format", "stop_sequences_still_forwarded"]


def schema_of(fwd):
    rf = (fwd or {}).get("response_format") or {}
    assert rf.get("type") == "json_schema", f"response_format={rf!r}"
    return (rf.get("json_schema") or {}).get("schema") or {}


try:
    with Upstream(respond) as up, gomodel.Gateway(up.url + "/v1") as gw:
        def send(**extra):
            up.reset()
            st, body, raw = gw.post_json("/v1/messages", messages_body("order 2 of A1", **extra))
            cap = up.last("/chat/completions")
            return st, raw, (cap.json if cap else None)
        cfg = send(output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
        fmt = send(output_format={"type": "json_schema", "schema": SCHEMA})
        plain = send()
        stop = send(stop_sequences=["END-K9"])
        up.reset()
        oai_status, _, _ = gw.post_json("/v1/chat/completions", {"model": "captured-model", "messages": [{"role": "user", "content": "x"}],
                                                                 "response_format": {"type": "json_schema", "json_schema": {"name": "o", "schema": SCHEMA}}})
        oai = up.last("/chat/completions")
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("output_config_schema_forwarded"):
        assert cfg[0] == 200, f"status {cfg[0]}: {cfg[1][:200]!r}"
        assert schema_of(cfg[2]).get("required") == ["sku", "qty"], cfg[2]

    with R.test("output_format_schema_forwarded"):
        assert fmt[0] == 200, f"status {fmt[0]}"
        assert "qty" in schema_of(fmt[2]).get("properties", {}), fmt[2]

    with R.test("no_response_format_invented"):
        assert plain[0] == 200 and "response_format" not in plain[2], plain[2]

    with R.test("openai_route_keeps_response_format"):
        assert oai_status == 200 and (oai.json.get("response_format") or {}).get("type") == "json_schema", oai.json

    with R.test("stop_sequences_still_forwarded"):
        assert stop[0] == 200 and stop[2].get("stop") in (["END-K9"], "END-K9"), stop[2]

code, out = run("go test -count=1 ./internal/anthropicapi/", cwd="/work/repo", timeout=900)
R.check("go_unit_tests_anthropicapi", code == 0, out)
R.write()
