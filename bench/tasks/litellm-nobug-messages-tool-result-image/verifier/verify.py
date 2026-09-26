"""Hidden verifier (no-bug task): tool-result images already reach the Responses backend."""

import json
import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import litellm  # noqa: E402
from upstream import proxy_config, respond  # noqa: E402

R = Results()
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC"
BEHAVIOR = ["tool_result_image_forwarded", "tool_result_text_forwarded", "call_id_preserved"]

try:
    with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
        status, body, raw = proxy.post_json("/v1/messages", {"model": "mock", "max_tokens": 64,
            "tools": [{"name": "screenshot", "description": "Capture", "input_schema": {"type": "object", "properties": {}}}],
            "messages": [
                {"role": "user", "content": "describe the screen"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_k9", "name": "screenshot", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_k9", "content": [
                    {"type": "text", "text": "capture-k9"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}]}]}]})
        cap = up.last("/responses")
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    items = (cap.json or {}).get("input", []) if cap else []
    outputs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]

    with R.test("tool_result_image_forwarded"):
        assert status == 200, f"status {status}"
        assert PNG in json.dumps(items), "tool-result PNG bytes are not in the forwarded input"

    with R.test("tool_result_text_forwarded"):
        assert "capture-k9" in json.dumps(outputs), outputs

    with R.test("call_id_preserved"):
        calls = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call"]
        assert calls and calls[0].get("call_id") == "toolu_k9", calls
        assert outputs and outputs[0].get("call_id") == "toolu_k9", outputs

D = "tests/test_litellm/llms/anthropic/experimental_pass_through/responses_adapters"
code, out = run(f"python -m pytest -q -p no:cacheprovider {D}", cwd="/work/repo", timeout=900)
R.check("upstream_unit_tests_responses_adapters", code == 0, out)
R.write()
