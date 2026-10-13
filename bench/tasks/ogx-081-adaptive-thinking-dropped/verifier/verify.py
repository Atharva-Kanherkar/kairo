"""Hidden verifier: adaptive thinking must be honored or refused, never silently dropped."""

import json

from kairo_verify import Results, run
from rigs import ogx

R = Results()
REASONING_KEYS = ("reasoning_effort", "reasoning", "thinking", "enable_thinking")


def body(**extra):
    return {"model": ogx.MODEL, "max_tokens": 200, "messages": [{"role": "user", "content": "Outline a rollback plan."}],
            **extra}


def honored_or_refused(status, forwarded):
    """Pass when the request is refused with a 4xx before any upstream call, or when the
    forwarded chat request carries a reasoning setting."""
    if 400 <= status < 500:
        assert not forwarded, f"refused with {status} but still called the upstream: {forwarded}"
        return
    assert status == 200, f"unexpected status {status}"
    assert forwarded, "no upstream call"
    fwd = forwarded[-1]
    carried = {k: fwd[k] for k in REASONING_KEYS if k in fwd}
    extra = fwd.get("extra_body") or {}
    carried.update({k: extra[k] for k in REASONING_KEYS if isinstance(extra, dict) and k in extra})
    assert carried, f"HTTP 200 but no reasoning setting reached the upstream: {json.dumps(fwd)[:600]}"


try:
    with ogx.Rig() as rig:
        adaptive = rig.messages(body(thinking={"type": "adaptive"}))
        adaptive_effort = rig.messages(body(thinking={"type": "adaptive"}, output_config={"effort": "high"}))
        enabled = rig.messages(body(thinking={"type": "enabled", "budget_tokens": 2048}))
        disabled = rig.messages(body(thinking={"type": "disabled"}))
        plain = rig.messages(body())
except Exception as exc:  # the server failing to start fails every behavioral test
    R.fail_all(["adaptive_thinking_not_silently_dropped", "adaptive_thinking_with_effort_not_silently_dropped",
                "enabled_thinking_still_rejected", "disabled_thinking_forwards_request",
                "plain_request_forwards_request"], f"OGX rig failed: {exc}")
else:
    with R.test("adaptive_thinking_not_silently_dropped"):
        honored_or_refused(adaptive[0], adaptive[3])

    with R.test("adaptive_thinking_with_effort_not_silently_dropped"):
        honored_or_refused(adaptive_effort[0], adaptive_effort[3])

    with R.test("enabled_thinking_still_rejected"):
        status, parsed, raw, forwarded = enabled
        assert status == 400, f"status {status}: {raw[:300]!r}"
        assert not forwarded, "enabled thinking reached the upstream"

    with R.test("disabled_thinking_forwards_request"):
        status, parsed, raw, forwarded = disabled
        assert status == 200 and forwarded, f"status {status}: {raw[:300]!r}"
        assert forwarded[-1]["messages"][-1]["content"] == "Outline a rollback plan."

    with R.test("plain_request_forwards_request"):
        status, parsed, raw, forwarded = plain
        assert status == 200 and forwarded, f"status {status}: {raw[:300]!r}"
        assert parsed["content"][0]["text"] == "Synthetic control response.", parsed
        assert forwarded[-1]["model"] == "mock-gpt", forwarded[-1]

code, out = run("python -m pytest -q -p no:cacheprovider tests/unit/providers/utils/inference/test_anthropic_translation.py",
                cwd="/work/repo", timeout=600)
R.check("upstream_unit_tests_anthropic_translation", code == 0, out)
R.write()
