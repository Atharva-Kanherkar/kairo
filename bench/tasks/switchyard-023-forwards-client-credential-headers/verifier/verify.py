"""Hidden verifier: caller credential and tenant headers must not be forwarded upstream."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder  # noqa: E402

R = Results()
CALLER = {"api-key": "kairo-caller-apikey-11", "OpenAI-Organization": "org-kairo-caller-12", "OpenAI-Project": "proj-kairo-caller-13",
          "Authorization": "Bearer kairo-caller-bearer-14", "x-api-key": "kairo-caller-xapi-15", "x-request-trace": "trace-kairo-16"}
BEHAVIOR = ["client_api_key_header_not_forwarded", "client_openai_org_and_project_not_forwarded",
            "client_authorization_and_x_api_key_still_stripped", "configured_credentials_still_sent",
            "benign_metadata_header_still_forwarded"]
try:
    with Upstream(make_responder()) as up:
        cfg = switchyard.passthrough(up.url + "/v1", "openai_chat", client={"api_key_env": "KAIRO_BACKEND_KEY"})
        with switchyard.Gateway(cfg, env={"KAIRO_BACKEND_KEY": "sk-kairo-backend-17"}) as gw:
            s, _, raw = gw.post_json("/v1/chat/completions", {"model": "captured-model", "messages": [{"role": "user", "content": "hi"}]}, CALLER)
            h = up.last().headers if up.last() else {}
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    sent = "\n".join(f"{k}: {v}" for k, v in h.items())

    with R.test("client_api_key_header_not_forwarded"):
        assert s == 200, f"status {s}: {raw[:200]!r}"
        assert CALLER["api-key"] not in sent, "caller api-key reached the upstream"

    with R.test("client_openai_org_and_project_not_forwarded"):
        leaked = [k for k in ("OpenAI-Organization", "OpenAI-Project") if CALLER[k] in sent]
        assert not leaked, f"forwarded {leaked}"

    with R.test("client_authorization_and_x_api_key_still_stripped"):
        assert "kairo-caller-bearer-14" not in sent and CALLER["x-api-key"] not in sent, sent

    with R.test("configured_credentials_still_sent"):
        assert h.get("authorization") == "Bearer sk-kairo-backend-17", h.get("authorization")

    with R.test("benign_metadata_header_still_forwarded"):
        assert h.get("x-request-trace") == "trace-kairo-16", sorted(h)

code, out = run("cargo test --locked -q -p switchyard-llm-client", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_llm_client", code == 0, out)
R.write()
