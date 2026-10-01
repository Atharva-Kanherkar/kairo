"""Hidden verifier: every executed auto tool call must be reported to the client, even for repeated names."""

import json
import sys
import time

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from mcp_server import MCPServer, marker  # noqa: E402
from rigs import bifrost  # noqa: E402
from upstream import PLANS, chat_body, gateway_config, respond  # noqa: E402

R = Results()
BEHAVIOR = ["repeated_tool_results_all_reported", "each_auto_call_executed_once", "distinct_tool_results_all_reported",
            "single_tool_result_reported", "pending_manual_call_returned"]


def content_of(body):
    msg = ((body or {}).get("choices") or [{}])[0].get("message") or {}
    return msg, json.dumps(msg.get("content"))


try:
    with Upstream(respond) as up, MCPServer() as mcp, bifrost.Gateway(gateway_config(up.url, mcp.url)) as gw:
        out = {}
        for scenario in ("repeated", "distinct", "single"):
            before = len(mcp.executions)
            status, body, raw = gw.post_json("/v1/chat/completions", chat_body(scenario))
            time.sleep(0.2)
            out[scenario] = (status, body, raw, mcp.executions[before:])
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    def reported(scenario):
        status, body, raw, execs = out[scenario]
        assert status == 200, f"{scenario}: status {status}: {raw[:300]!r}"
        msg, text = content_of(body)
        return [marker(t, {"invoice": inv}) for t, inv in PLANS[scenario] if marker(t, {"invoice": inv}) in text], text

    with R.test("repeated_tool_results_all_reported"):
        found, text = reported("repeated")
        assert found == ["EFFECT-charge-alpha", "EFFECT-charge-beta"], f"reported {found}: {text[:600]}"

    with R.test("each_auto_call_executed_once"):
        for scenario, plan in PLANS.items():
            execs = [(e["tool"], e["arguments"].get("invoice")) for e in out[scenario][3]]
            assert sorted(execs) == sorted(plan), f"{scenario}: executions {execs}"

    with R.test("distinct_tool_results_all_reported"):
        found, text = reported("distinct")
        assert found == ["EFFECT-charge-alpha", "EFFECT-credit-beta"], f"reported {found}: {text[:600]}"

    with R.test("single_tool_result_reported"):
        found, text = reported("single")
        assert found == ["EFFECT-charge-alpha"], f"reported {found}: {text[:600]}"

    with R.test("pending_manual_call_returned"):
        for scenario in PLANS:
            msg, _ = content_of(out[scenario][1])
            names = [(c.get("function") or {}).get("name", "") for c in msg.get("tool_calls") or []]
            assert any(n.endswith("review") for n in names), f"{scenario}: pending calls {names}"
            assert not any(n.endswith(("charge", "credit")) for n in names), f"{scenario}: executed calls returned as pending {names}"

code, out_text = run("go test -count=1 ./mcp/", cwd="/work/repo/core", timeout=1200)
R.check("go_unit_tests_mcp", code == 0, out_text)
R.write()
