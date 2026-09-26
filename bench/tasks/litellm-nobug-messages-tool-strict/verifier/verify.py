"""Hidden verifier (no-bug task): tools[].strict already reaches the Responses backend."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import litellm  # noqa: E402
from upstream import proxy_config, respond  # noqa: E402

R = Results()
SCHEMA = {"type": "object", "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}},
          "required": ["sku", "qty"], "additionalProperties": False}
BEHAVIOR = ["strict_true_forwarded", "strict_absent_not_invented", "tool_schema_forwarded"]


def send(proxy, up, tools):
    up.reset()
    status, body, raw = proxy.post_json("/v1/messages", {"model": "mock", "max_tokens": 64, "tools": tools,
                                                         "messages": [{"role": "user", "content": "order 2 of A1"}]})
    cap = up.last("/responses")
    assert status == 200 and cap is not None, f"status {status}"
    return {t.get("name"): t for t in cap.json.get("tools", [])}


try:
    with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
        strict = send(proxy, up, [{"name": "place_order", "description": "Order", "strict": True, "input_schema": SCHEMA}])
        loose = send(proxy, up, [{"name": "place_order", "description": "Order", "input_schema": SCHEMA}])
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("strict_true_forwarded"):
        assert strict["place_order"].get("strict") is True, strict

    with R.test("strict_absent_not_invented"):
        assert loose["place_order"].get("strict") is not True, loose

    with R.test("tool_schema_forwarded"):
        assert strict["place_order"].get("parameters", {}).get("required") == ["sku", "qty"], strict
        assert strict["place_order"].get("type") == "function", strict

D = "tests/test_litellm/llms/anthropic/experimental_pass_through/responses_adapters"
code, out = run(f"python -m pytest -q -p no:cacheprovider {D}", cwd="/work/repo", timeout=900)
R.check("upstream_unit_tests_responses_adapters", code == 0, out)
R.write()
