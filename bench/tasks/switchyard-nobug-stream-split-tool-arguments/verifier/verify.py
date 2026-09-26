"""Hidden verifier (no-bug task): split tool-call argument deltas are already reassembled byte-exactly."""

import json
import sys

from kairo_verify import Results, Upstream, run, sse_events

sys.path.insert(0, "/work/kairo")
from rigs import switchyard  # noqa: E402
from upstream import make_responder, messages_body  # noqa: E402

import anthropic_sse as sse  # noqa: E402

R = Results()
ARGS = {"file": "a/b.py", "body": "line1\nline2 \"quoted\" \\ backslash\n", "emoji": "\U0001F600 ok", "n": [1, 2, 3]}
BEHAVIOR = ["split_arguments_reassembled_exactly", "tool_use_identity_intact", "stream_ends_with_tool_use"]
try:
    with Upstream(make_responder(ARGS, pieces=29)) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
        status, _, raw = gw.post("/v1/messages", messages_body())
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    evs = [e["json"] for e in sse_events(raw) if e["json"]]
    partial = "".join((e.get("delta") or {}).get("partial_json", "") for e in evs
                      if e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "input_json_delta")
    starts = [e.get("content_block") for e in evs if e.get("type") == "content_block_start"]

    with R.test("split_arguments_reassembled_exactly"):
        assert status == 200, f"status {status}"
        assert json.loads(partial) == ARGS, f"reassembled: {partial[:300]!r}"

    with R.test("tool_use_identity_intact"):
        uses = [b for b in starts if b and b.get("type") == "tool_use"]
        assert len(uses) == 1 and uses[0].get("id") == "call_split" and uses[0].get("name") == "apply_patch", starts

    with R.test("stream_ends_with_tool_use"):
        assert sse.stop_reason(raw) == "tool_use", sse.stop_reason(raw)

code, out = run("cargo test --locked -q -p switchyard-translation", cwd="/work/repo", timeout=2400)
R.check("cargo_tests_translation", code == 0, out)
R.write()
