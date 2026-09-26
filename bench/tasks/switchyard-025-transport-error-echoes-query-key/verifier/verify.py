"""Hidden verifier: transport errors must not disclose base_url query credentials."""

import sys

from kairo_verify import Results, Upstream, free_port, run

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

R = Results()
KEY = "kairo-query-secret-A7Q2"
BEHAVIOR = ["chat_502_omits_query_key", "messages_502_omits_query_key", "unreachable_upstream_still_502",
            "reachable_upstream_still_works"]
try:
    closed = free_port()
    with switchyard.Gateway(switchyard.passthrough(f"http://127.0.0.1:{closed}/v1?key={KEY}&alt=json")) as gw:
        chat = gw.post_json("/v1/chat/completions", {"model": "captured-model", "messages": [{"role": "user", "content": "hi"}]})
        msgs = gw.post_json("/v1/messages", messages_body("hi"))
    with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(f"{up.url}/v1?key={KEY}")) as gw:
        ok = gw.post_json("/v1/chat/completions", {"model": "captured-model", "messages": [{"role": "user", "content": "hi"}]})
        ok_cap = up.last()
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("chat_502_omits_query_key"):
        assert KEY not in chat[2].decode("utf-8", "replace"), "the /v1/chat/completions error body contains the key"

    with R.test("messages_502_omits_query_key"):
        assert KEY not in msgs[2].decode("utf-8", "replace"), "the /v1/messages error body contains the key"

    with R.test("unreachable_upstream_still_502"):
        assert chat[0] == 502 and msgs[0] == 502, (chat[0], msgs[0])
        assert chat[1] and chat[1].get("error"), chat[2][:200]

    with R.test("reachable_upstream_still_works"):
        assert ok[0] == 200 and ok_cap is not None and KEY in ok_cap.path, (ok[0], ok_cap.path if ok_cap else None)

code, out = run("cargo test --locked -q -p switchyard-llm-client", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_llm_client", code == 0, out)
R.write()
