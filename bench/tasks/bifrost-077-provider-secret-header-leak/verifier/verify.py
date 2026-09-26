"""Hidden verifier: provider credentials must not reach a virtual-key caller in any form."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import bifrost  # noqa: E402
from upstream import VIRTUAL_KEY, gateway_config, respond, responses_body  # noqa: E402

R = Results()
SECRET, TOKEN, TRACE = "kairo-provider-secret-5521", "kairo-gateway-token-7719", "kairo-trace-3306"
BEHAVIOR = ["response_headers_omit_provider_secrets", "response_body_omits_provider_secrets",
            "authorization_header_still_stripped", "benign_provider_header_preserved",
            "unauthenticated_request_rejected", "virtual_key_cannot_read_provider_config"]
try:
    with Upstream(respond) as up:
        cfg = gateway_config(up.url, {"X-Provider-Secret": SECRET, "X-Gateway-Token": TOKEN, "X-Safe-Trace": TRACE})
        with bifrost.Gateway(cfg) as gw:
            ok = gw.post("/v1/responses", responses_body(), {"x-bf-vk": VIRTUAL_KEY})
            anon = gw.post("/v1/responses", responses_body())
            providers = gw.get("/api/providers", {"x-bf-vk": VIRTUAL_KEY})
            upstream_saw = up.last("/responses")
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    status, headers, raw = ok
    header_text = "\n".join(f"{k}: {v}" for k, v in headers.items())
    body_text = raw.decode("utf-8", "replace")

    with R.test("response_headers_omit_provider_secrets"):
        assert status == 200, f"status {status}: {body_text[:300]}"
        assert upstream_saw is not None and upstream_saw.headers.get("x-provider-secret") == SECRET, \
            "the provider no longer receives its configured extra header"
        leaked = [n for n, v in (("X-Provider-Secret", SECRET), ("X-Gateway-Token", TOKEN)) if v in header_text]
        assert not leaked, f"HTTP response headers carry {leaked}"

    with R.test("response_body_omits_provider_secrets"):
        leaked = [n for n, v in (("X-Provider-Secret", SECRET), ("X-Gateway-Token", TOKEN)) if v in body_text]
        assert not leaked, f"response body carries {leaked}"

    with R.test("authorization_header_still_stripped"):
        assert "upstream-authorization-control" not in header_text + body_text, "upstream Authorization re-served"

    with R.test("benign_provider_header_preserved"):
        assert headers.get("x-safe-trace") == TRACE, f"benign header lost: {sorted(headers)}"

    with R.test("unauthenticated_request_rejected"):
        assert anon[0] in (401, 403), f"status {anon[0]}"

    with R.test("virtual_key_cannot_read_provider_config"):
        assert providers[0] in (401, 403), f"GET /api/providers with a virtual key returned {providers[0]}"

code, out = run("go test -count=1 ./providers/utils/", cwd="/work/repo/core", timeout=1200)
R.check("go_unit_tests_provider_utils", code == 0, out)
R.write()
