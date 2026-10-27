"""Hidden verifier (no-bug task): body credentials never replace or add upstream auth headers."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder  # noqa: E402

R = Results()
BEHAVIOR = ["body_api_key_does_not_replace_backend_credential", "body_credentials_not_turned_into_headers",
            "request_still_served"]
try:
    with Upstream(make_responder()) as up:
        cfg = switchyard.passthrough(f"{up.url}/v1", "openai_chat", client={"api_key_env": "KAIRO_BACKEND_KEY"})
        with switchyard.Gateway(cfg, env={"KAIRO_BACKEND_KEY": "sk-kairo-backend-21"}) as gw:
            status, body, raw = gw.post_json("/v1/chat/completions", {
                "model": "captured-model", "api_key": "sk-kairo-attacker-22", "organization": "org-kairo-attacker-23",
                "extra_headers": {"Authorization": "Bearer sk-kairo-attacker-24", "api-key": "sk-kairo-attacker-25"},
                "messages": [{"role": "user", "content": "hi"}]})
            cap = up.last()
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    headers = cap.headers if cap else {}
    header_text = "\n".join(f"{k}: {v}" for k, v in headers.items())

    with R.test("body_api_key_does_not_replace_backend_credential"):
        assert headers.get("authorization") == "Bearer sk-kairo-backend-21", headers.get("authorization")

    with R.test("body_credentials_not_turned_into_headers"):
        leaked = [v for v in ("sk-kairo-attacker-22", "org-kairo-attacker-23", "sk-kairo-attacker-24", "sk-kairo-attacker-25")
                  if v in header_text]
        assert not leaked, f"body credentials became headers: {leaked}"

    with R.test("request_still_served"):
        assert status == 200 and body["choices"][0]["message"]["content"] == "ok", raw[:200]

code, out = run("cargo test --locked -q -p switchyard-llm-client", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_llm_client", code == 0, out)
R.write()
