"""Hidden verifier: /health must not disclose credentials from any deployment field."""

import json
import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import litellm  # noqa: E402
from upstream import proxy_config, respond  # noqa: E402

R = Results()
C = {"api_key": "sk-kairo-secret-apikey-81", "extra_auth": "kairo-secret-extra-auth-82",
     "extra_goog": "kairo-secret-goog-83", "extra_custom": "kairo-secret-gateway-84",
     "headers": "kairo-secret-headers-85", "aws_session_token": "kairo-secret-aws-session-86"}
BEHAVIOR = ["health_hides_extra_headers_values", "health_hides_headers_field_values", "health_hides_aws_session_token",
            "health_hides_api_key", "health_reports_endpoint_status", "chat_still_sends_extra_headers"]

try:
    with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url, C)) as proxy:
        chat = proxy.post_json("/chat/completions", {"model": "mock", "messages": [{"role": "user", "content": "hi"}]})
        chat_cap = up.last("/chat/completions")
        h_status, _, h_raw = proxy.get("/health")
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    text = h_raw.decode("utf-8", "replace")
    try:
        health = json.loads(text)
    except json.JSONDecodeError:
        health = None

    with R.test("health_hides_extra_headers_values"):
        leaked = [k for k in ("extra_auth", "extra_goog", "extra_custom") if C[k] in text]
        assert not leaked, f"/health disclosed extra_headers values: {leaked}"

    with R.test("health_hides_headers_field_values"):
        assert C["headers"] not in text, "/health disclosed the deployment `headers` value"

    with R.test("health_hides_aws_session_token"):
        assert C["aws_session_token"] not in text, "/health disclosed aws_session_token"

    with R.test("health_hides_api_key"):
        assert C["api_key"] not in text, "/health disclosed api_key"

    with R.test("health_reports_endpoint_status"):
        assert h_status == 200 and isinstance(health, dict), f"status {h_status}: {text[:300]}"
        healthy = health.get("healthy_endpoints") or []
        assert health.get("healthy_count") == 1 and len(healthy) == 1, f"health body: {text[:500]}"
        assert healthy[0].get("model") == "openai/mockmodel", healthy[0]

    with R.test("chat_still_sends_extra_headers"):
        status, body, raw = chat
        assert status == 200, f"chat status {status}: {raw[:300]!r}"
        assert chat_cap is not None, "chat did not reach the upstream"
        h = chat_cap.headers
        assert h.get("x-goog-api-key") == C["extra_goog"] and h.get("x-gateway-token") == C["extra_custom"], \
            f"deployment extra_headers no longer reach the provider: {sorted(h)}"

F = "tests/test_litellm/proxy/health_endpoints/test_health_endpoints.py"
code, out = run(f"python -m pytest -q -p no:cacheprovider {F}", cwd="/work/repo", timeout=900)
R.check("upstream_unit_tests_health_endpoints", code == 0, out)
R.write()
