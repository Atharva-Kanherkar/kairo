"""Hidden verifier: Anthropic tool_choice must map to a value OpenAI accepts, on both routes."""

import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import bifrost  # noqa: E402
from upstream import messages_body, respond  # noqa: E402

R = Results()
BEHAVIOR = ["tool_choice_any_mapped_on_responses_route", "tool_choice_any_mapped_on_chat_route",
            "tool_choice_auto_preserved", "tool_choice_named_tool_preserved", "tool_call_reply_translated"]
ROUTES = {"responses": "openai/gpt-4o", "chat": "chatonly/gpt-4o"}


def send(gw, up, model, tool_choice):
    up.reset()
    status, body, raw = gw.post_json("/anthropic/v1/messages", messages_body(model, tool_choice))
    cap = up.last("/responses") or up.last("/chat/completions")
    return status, body, (cap.json if cap else None), (cap.path if cap else None)


def named(route, fwd):
    tc = fwd.get("tool_choice")
    if route == "responses":
        return tc == {"type": "function", "name": "get_weather"}
    return tc == {"type": "function", "function": {"name": "get_weather"}}


try:
    with Upstream(respond) as up:
        cfg = bifrost.config({"openai": bifrost.openai_provider(up.url), "chatonly": bifrost.chat_only_provider(up.url)})
        with bifrost.Gateway(cfg) as gw:
            res = {(route, kind): send(gw, up, model, tc)
                   for route, model in ROUTES.items()
                   for kind, tc in (("any", {"type": "any"}), ("auto", {"type": "auto"}),
                                    ("tool", {"type": "tool", "name": "get_weather"}))}
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    for route in ROUTES:
        with R.test(f"tool_choice_any_mapped_on_{route}_route"):
            status, body, fwd, path = res[(route, "any")]
            assert fwd is not None, f"nothing forwarded (client status {status})"
            assert fwd.get("tool_choice") == "required", f"{path}: tool_choice={fwd.get('tool_choice')!r}"

    with R.test("tool_choice_auto_preserved"):
        for route in ROUTES:
            status, body, fwd, path = res[(route, "auto")]
            assert fwd and fwd.get("tool_choice") in ("auto", None), f"{route}: {fwd and fwd.get('tool_choice')!r}"

    with R.test("tool_choice_named_tool_preserved"):
        for route in ROUTES:
            status, body, fwd, path = res[(route, "tool")]
            assert fwd and named(route, fwd), f"{route}: {fwd and fwd.get('tool_choice')!r}"

    with R.test("tool_call_reply_translated"):
        for route in ROUTES:
            status, body, fwd, path = res[(route, "auto")]
            assert status == 200 and body, f"{route}: status {status}"
            uses = [b for b in body.get("content", []) if b.get("type") == "tool_use"]
            assert uses and uses[0]["name"] == "get_weather" and uses[0]["input"] == {"location": "Tokyo"}, body
            assert body.get("stop_reason") == "tool_use", body.get("stop_reason")

for pkg in ("openai", "anthropic"):
    code, out = run(f"go test -count=1 ./providers/{pkg}/", cwd="/work/repo/core", timeout=900)
    R.check(f"go_unit_tests_{pkg}_provider", code == 0, out)
R.write()
