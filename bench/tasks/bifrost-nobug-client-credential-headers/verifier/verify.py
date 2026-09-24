"""Hidden verifier (no-bug task): client credential headers already stay out of the upstream request."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import bifrost  # noqa: E402
from upstream import respond  # noqa: E402

R = Results()
CLIENT = {"x-api-key": "kairo-client-xapi-1", "api-key": "kairo-client-apikey-2", "OpenAI-Organization": "org-kairo-3",
          "OpenAI-Project": "proj-kairo-4", "Authorization": "Bearer kairo-client-bearer-5"}
BEHAVIOR = ["client_credential_headers_not_forwarded", "deployment_key_used_upstream", "plain_request_succeeds"]
try:
    with Upstream(respond) as up, bifrost.Gateway(bifrost.config({"openai": bifrost.openai_provider(up.url)})) as gw:
        body = {"model": "openai/gpt-4o", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}
        with_headers = gw.post_json("/anthropic/v1/messages", body, CLIENT)
        cap = up.last("/responses")
        plain = gw.post_json("/v1/chat/completions", {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    sent = "\n".join(f"{k}: {v}" for k, v in (cap.headers.items() if cap else []))

    with R.test("client_credential_headers_not_forwarded"):
        assert cap is not None, f"nothing reached the upstream (status {with_headers[0]})"
        leaked = [k for k, v in CLIENT.items() if v.split()[-1] in sent]
        assert not leaked, f"forwarded client credentials: {leaked}"

    with R.test("deployment_key_used_upstream"):
        assert cap.headers.get("authorization") == "Bearer sk-kairo-provider", cap.headers.get("authorization")

    with R.test("plain_request_succeeds"):
        assert with_headers[0] == 200 and plain[0] == 200, (with_headers[0], plain[0])

code, out = run("go test -count=1 ./providers/utils/", cwd="/work/repo/core", timeout=1200)
R.check("go_unit_tests_provider_utils", code == 0, out)
R.write()
