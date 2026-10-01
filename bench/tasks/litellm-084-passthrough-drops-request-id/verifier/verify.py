"""Hidden verifier: the OpenAI pass-through must forward provider request fields intact."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import litellm  # noqa: E402
from upstream import respond  # noqa: E402

R = Results()
KEY = "sk-kairo-deployment-key-7731"
BEHAVIOR = ["alpha_search_id_forwarded", "passthrough_id_forwarded_on_other_endpoint",
            "passthrough_forwards_other_fields", "passthrough_uses_deployment_key",
            "passthrough_keeps_litellm_control_params_internal"]

search = {
    "id": "search-run-5541",
    "model": "gpt-5.6-mini",
    "commands": {"search_query": [{"q": "kairo benchmark"}, {"q": "tool call replay"}]},
    "settings": {"search_context_size": "medium"},
    "max_output_tokens": 64,
}
other = {"id": "job-2208", "input": "hello", "model": "gpt-5.6-mini"}
control = {"id": "search-run-9", "model": "gpt-5.6-mini", "commands": {"search_query": [{"q": "x"}]},
           "mock_response": "should not reach the provider", "litellm_metadata": {"team": "kairo"}}

try:
    with Upstream(respond) as up:
        with litellm.Proxy(env={"OPENAI_API_BASE": up.url, "OPENAI_API_KEY": KEY}) as proxy:
            s1, b1, r1 = proxy.post_json("/openai_passthrough/v1/alpha/search", search)
            c1 = up.last("/alpha/search")
            up.reset()
            s2, b2, r2 = proxy.post_json("/openai_passthrough/v1/files/uploads/finalize", other)
            c2 = up.last("/finalize")
            up.reset()
            s3, b3, r3 = proxy.post_json("/openai_passthrough/v1/alpha/search", control)
            c3 = up.last("/alpha/search")
except Exception as exc:
    R.fail_all(BEHAVIOR, f"proxy rig failed: {exc}")
else:
    with R.test("alpha_search_id_forwarded"):
        assert c1 is not None, "nothing reached the upstream"
        assert c1.json.get("id") == "search-run-5541", f"forwarded body: {c1.json}"
        assert s1 == 200, f"client got {s1}: {r1[:300]!r}"

    with R.test("passthrough_id_forwarded_on_other_endpoint"):
        assert c2 is not None, "nothing reached the upstream"
        assert c2.json.get("id") == "job-2208", f"forwarded body: {c2.json}"

    with R.test("passthrough_forwards_other_fields"):
        assert c1 is not None and c1.json is not None
        for k in ("model", "commands", "settings", "max_output_tokens"):
            assert c1.json.get(k) == search[k], f"{k} changed: {c1.json.get(k)!r}"
        assert c2 is not None and c2.json.get("input") == "hello", c2.json if c2 else None

    with R.test("passthrough_uses_deployment_key"):
        assert c1 is not None
        auth = c1.headers.get("authorization", "")
        assert auth == f"Bearer {KEY}", f"authorization header was {auth!r}"
        assert litellm.MASTER_KEY not in str(c1.headers), "the proxy master key reached the provider"

    with R.test("passthrough_keeps_litellm_control_params_internal"):
        assert c3 is not None, "nothing reached the upstream"
        assert "mock_response" not in c3.json and "litellm_metadata" not in c3.json, f"forwarded: {c3.json}"
        assert c3.json.get("commands") == control["commands"], c3.json

code, out = run("python -m pytest -q -p no:cacheprovider tests/pass_through_unit_tests/test_pass_through_unit_tests.py",
                cwd="/work/repo", timeout=900)
R.check("upstream_unit_tests_pass_through", code == 0, out)
R.write()
