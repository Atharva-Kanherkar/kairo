"""Hidden verifier: a delivered tool call must never be replayed by a mid-stream fallback."""

import json
import re
import sys

from kairo_verify import Results, Upstream, run, sse_events

sys.path.insert(0, "/work/kairo")
from rigs import litellm  # noqa: E402
from upstream import make_responder, proxy_config, request_body  # noqa: E402

R = Results()
BEHAVIOR = ["delivered_tool_call_not_replayed_after_error_event", "delivered_tool_call_not_replayed_after_response_failed",
            "delivered_tool_call_not_replayed_before_reasoning_fallback", "failure_before_output_still_falls_back",
            "no_fault_stream_unchanged", "transport_drop_surfaces_failure"]


def analyze(raw):
    evs = [e["json"] for e in sse_events(raw) if e["json"]]
    types = [e.get("type") for e in evs]
    calls = [((e.get("item") or {}).get("call_id")) for e in evs
             if e.get("type") == "response.output_item.done" and (e.get("item") or {}).get("type") == "function_call"]
    return {"types": types, "calls": calls, "created": types.count("response.created"),
            "completed": types.count("response.completed"),
            "failed": "response.failed" in types or "error" in types}


def run_case(proxy, up, model):
    before = len([c for c in up.requests if c.method == "POST"])
    status, _, raw = proxy.post("/v1/responses", request_body(model), timeout=120)
    posts = [c.json.get("model") for c in up.requests if c.method == "POST"][before:]
    return status, analyze(raw), posts, raw


def assert_not_replayed(case):
    status, a, posts, raw = case
    assert a["calls"] == ["call_primary"], f"client received function calls {a['calls']} (upstream calls {posts})"
    assert a["created"] == 1, f"{a['created']} response.created events in one stream"
    assert a["failed"], f"the primary failure was not surfaced to the client: {a['types']}"
    assert a["completed"] == 0, "a failed turn was reported as response.completed"


try:
    with Upstream(make_responder()) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
        cases = {m: run_case(proxy, up, m) for m in
                 ("agent-error", "agent-failed", "agent-reasoning", "agent-prefail", "agent-nofault", "agent-drop")}
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("delivered_tool_call_not_replayed_after_error_event"):
        assert_not_replayed(cases["agent-error"])

    with R.test("delivered_tool_call_not_replayed_after_response_failed"):
        assert_not_replayed(cases["agent-failed"])

    with R.test("delivered_tool_call_not_replayed_before_reasoning_fallback"):
        assert_not_replayed(cases["agent-reasoning"])

    with R.test("failure_before_output_still_falls_back"):
        status, a, posts, raw = cases["agent-prefail"]
        assert posts == ["primary-prefail", "fallback-tool"], f"upstream calls {posts}"
        assert a["calls"] == ["call_fallback"], f"client received function calls {a['calls']}"
        assert a["completed"] >= 1, f"fallback did not complete: {a['types']}"

    with R.test("no_fault_stream_unchanged"):
        status, a, posts, raw = cases["agent-nofault"]
        assert status == 200 and posts == ["primary-nofault"], f"status {status}, upstream calls {posts}"
        assert a["calls"] == ["call_primary"] and a["created"] == 1 and a["completed"] == 1, a

    with R.test("transport_drop_surfaces_failure"):
        status, a, posts, raw = cases["agent-drop"]
        assert "call_fallback" not in a["calls"], f"fallback replayed after a transport drop: {a['calls']}"
        assert a["calls"] == ["call_primary"], a["calls"]
        assert a["failed"], f"transport drop was not surfaced: {a['types']}"

F = "tests/router_unit_tests/test_router_aresponses_streaming_fallback.py"
code, out = run(f"python -m pytest -q -p no:cacheprovider {F}", cwd="/work/repo", timeout=900)
R.check("upstream_unit_tests_responses_streaming_fallback", code == 0, out)
R.write()
