"""Hidden verifier: inline system/developer Responses items must keep instruction roles on the Chat hop."""

import json
import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder  # noqa: E402

R = Results()
SYS, DEV, USER, ASSIST = "Kairo-sys-S5 be terse.", "Kairo-dev-D5 no deletes.", "Kairo-user-U5 tidy up", "Kairo-assistant-A5"
BEHAVIOR = ["inline_system_item_is_not_user", "inline_developer_item_is_not_user", "string_instructions_become_system",
            "user_and_assistant_items_keep_roles"]


def role_of(messages, needle):
    for m in messages:
        if needle in json.dumps(m.get("content")):
            return m.get("role")
    return None


try:
    with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
        s1, _, r1 = gw.post_json("/v1/responses", {"model": "captured-model", "input": [
            {"type": "message", "role": "system", "content": SYS},
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": DEV}]},
            {"type": "message", "role": "user", "content": USER},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": ASSIST}]},
            {"type": "message", "role": "user", "content": "continue"}]})
        m1 = up.last().json.get("messages", [])
        s2, _, r2 = gw.post_json("/v1/responses", {"model": "captured-model", "instructions": SYS, "input": USER})
        m2 = up.last().json.get("messages", [])
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("inline_system_item_is_not_user"):
        assert s1 == 200, f"status {s1}: {r1[:200]!r}"
        assert role_of(m1, SYS) in ("system", "developer"), f"system item forwarded as {role_of(m1, SYS)!r}"

    with R.test("inline_developer_item_is_not_user"):
        assert role_of(m1, DEV) in ("system", "developer"), f"developer item forwarded as {role_of(m1, DEV)!r}"

    with R.test("string_instructions_become_system"):
        assert s2 == 200 and role_of(m2, SYS) in ("system", "developer"), m2

    with R.test("user_and_assistant_items_keep_roles"):
        assert role_of(m1, USER) == "user" and role_of(m1, ASSIST) == "assistant", m1
        assert m1[-1].get("role") == "user" and "continue" in json.dumps(m1[-1].get("content")), m1[-1]

code, out = run("cargo test --locked -q -p switchyard-translation", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_translation", code == 0, out)
R.write()
