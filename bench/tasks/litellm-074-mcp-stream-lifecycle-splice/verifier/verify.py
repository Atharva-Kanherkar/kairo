"""Hidden verifier: one MCP auto-executed turn must stream as one Responses lifecycle."""

import json
import os
import sys
import tempfile

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import litellm  # noqa: E402
from upstream import make_responder, proxy_config, request_body  # noqa: E402

from lifecycle import events, lifecycle_problem  # noqa: E402

R = Results()
FINAL, CONTROL, ARG = "ANSWER_AFTER_TOOL_7Q", "PLAIN_ANSWER_3K", "KAIRO-ARG-55"
PROMPT = f"Use the echo tool with {ARG}, then answer."
BEHAVIOR = ["mcp_auto_execute_stream_is_one_lifecycle", "openai_sdk_stream_returns_final_answer",
            "mcp_tool_executed_exactly_once", "mcp_final_answer_reaches_client",
            "approval_required_stream_unchanged", "no_tool_stream_unchanged"]


def text_of(raw):
    return json.dumps(events(raw))


try:
    call_log = os.path.join(tempfile.mkdtemp(), "mcp-calls.jsonl")
    responder = make_responder(final_text=FINAL, control_text=CONTROL, argument=ARG)
    with Upstream(responder) as up, litellm.Proxy(config=proxy_config(up.url, call_log)) as proxy:
        s_auto, _, raw_auto = proxy.post("/v1/responses", request_body("auto", PROMPT))
        calls_after_auto = open(call_log).read().splitlines() if os.path.exists(call_log) else []
        s_appr, _, raw_appr = proxy.post("/v1/responses", request_body("approval", PROMPT))
        s_none, _, raw_none = proxy.post("/v1/responses", request_body("no-tool"))
        calls_total = open(call_log).read().splitlines() if os.path.exists(call_log) else []

        from openai import OpenAI
        client = OpenAI(base_url=f"{proxy.url}/v1", api_key=litellm.MASTER_KEY, max_retries=0, timeout=60)
        sdk_error, sdk_final = None, None
        try:
            kwargs = {k: v for k, v in request_body("auto", PROMPT).items() if k != "stream"}
            with client.responses.stream(**kwargs) as stream:
                for _ in stream:
                    pass
                sdk_final = stream.get_final_response().model_dump(mode="json")
        except Exception as exc:
            sdk_error = f"{type(exc).__name__}: {exc}"
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    with R.test("mcp_auto_execute_stream_is_one_lifecycle"):
        assert s_auto == 200, f"status {s_auto}"
        problem = lifecycle_problem(raw_auto)
        assert problem is None, problem

    with R.test("openai_sdk_stream_returns_final_answer"):
        assert sdk_error is None, f"OpenAI SDK raised {sdk_error}"
        assert FINAL in json.dumps(sdk_final), f"final response lacks the answer: {json.dumps(sdk_final)[:600]}"

    with R.test("mcp_tool_executed_exactly_once"):
        assert [json.loads(c) for c in calls_after_auto] == [{"tool": "echo", "value": ARG}], calls_after_auto

    with R.test("mcp_final_answer_reaches_client"):
        assert FINAL in text_of(raw_auto), "the follow-up answer is missing from the client stream"

    with R.test("approval_required_stream_unchanged"):
        assert s_appr == 200, f"status {s_appr}"
        assert lifecycle_problem(raw_appr) is None, lifecycle_problem(raw_appr)
        assert len(calls_total) == len(calls_after_auto), "require_approval=always executed the tool"
        assert FINAL not in text_of(raw_appr), "approval-required turn should stop before the follow-up round"

    with R.test("no_tool_stream_unchanged"):
        assert s_none == 200, f"status {s_none}"
        assert lifecycle_problem(raw_none) is None, lifecycle_problem(raw_none)
        assert CONTROL in text_of(raw_none), "plain answer missing"

# The two deselected tests pin the pre-fix two-lifecycle behavior; PR #40121 rewrote them.
F = "tests/test_litellm/responses/mcp/test_mcp_streaming_iterator.py"
code, out = run(f"python -m pytest -q -p no:cacheprovider {F} "
                f"--deselect {F}::test_continuation_id_is_final_round_not_interim_tool_call "
                f"--deselect {F}::test_second_round_tool_call_is_executed_and_reaches_final_text",
                cwd="/work/repo", timeout=900)
R.check("upstream_unit_tests_mcp_streaming_iterator", code == 0, out)
R.write()
