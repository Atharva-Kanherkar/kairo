"""Hidden verifier: Anthropic text documents must reach the OpenAI Responses backend."""

import json
import sys

from kairo_verify import Results, Upstream, run

sys.path.insert(0, "/work/kairo")
from rigs import litellm  # noqa: E402
from upstream import proxy_config, respond  # noqa: E402

R = Results()
DOC = "Kairo-doc-P7: the canary deploy at 14:03 UTC rolled back after error rate hit 4%."
PDF = "JVBERi0xLjQKJcfsj6IKMSAwIG9iago8PD4+CmVuZG9iagp0cmFpbGVyCjw8Pj4KJSVFT0YK"
BEHAVIOR = ["text_document_reaches_upstream", "content_document_reaches_upstream", "pdf_document_still_input_file",
            "url_document_still_input_file", "user_text_unchanged"]


def user_parts(body):
    parts = []
    for item in body.get("input") or []:
        if isinstance(item, dict) and item.get("role") == "user":
            c = item.get("content")
            parts += c if isinstance(c, list) else [{"type": "input_text", "text": c}]
    return parts


def send(proxy, up, blocks):
    up.reset()
    status, body, raw = proxy.post_json("/v1/messages", {"model": "mock", "max_tokens": 64,
                                                         "messages": [{"role": "user", "content": blocks}]})
    cap = up.last("/responses")
    assert status == 200, f"status {status}: {raw[:300]!r}"
    assert cap is not None and cap.json is not None, "nothing reached /v1/responses"
    return cap.json


intro = {"type": "text", "text": "Summarize the attached report."}
try:
    with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
        cases = {}
        for name, doc in {
            "text": {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": DOC}},
            "content": {"type": "document", "source": {"type": "content", "content": [{"type": "text", "text": DOC}]}},
            "pdf": {"type": "document", "title": "report.pdf",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": PDF}},
            "url": {"type": "document", "source": {"type": "url", "url": "https://example.com/report.pdf"}},
        }.items():
            try:
                cases[name] = send(proxy, up, [intro, doc])
            except AssertionError as exc:
                cases[name] = exc
        try:
            cases["plain"] = send(proxy, up, [intro])
        except AssertionError as exc:
            cases["plain"] = exc
except Exception as exc:
    R.fail_all(BEHAVIOR, f"rig failed: {exc}")
else:
    def parts(name):
        b = cases[name]
        if isinstance(b, AssertionError):
            raise b
        return user_parts(b)

    with R.test("text_document_reaches_upstream"):
        assert DOC in json.dumps(parts("text")), f"document text missing from forwarded input: {parts('text')}"

    with R.test("content_document_reaches_upstream"):
        assert DOC in json.dumps(parts("content")), f"document text missing from forwarded input: {parts('content')}"

    with R.test("pdf_document_still_input_file"):
        files = [p for p in parts("pdf") if p.get("type") == "input_file"]
        assert files and PDF in files[0].get("file_data", ""), parts("pdf")

    with R.test("url_document_still_input_file"):
        files = [p for p in parts("url") if p.get("type") == "input_file"]
        assert files and files[0].get("file_url") == "https://example.com/report.pdf", parts("url")

    with R.test("user_text_unchanged"):
        for name in ("plain", "text", "pdf"):
            texts = [p.get("text") for p in parts(name) if p.get("type") == "input_text"]
            assert "Summarize the attached report." in texts, f"{name}: {parts(name)}"

D = "tests/test_litellm/llms/anthropic/experimental_pass_through/responses_adapters"
code, out = run(f"python -m pytest -q -p no:cacheprovider {D}", cwd="/work/repo", timeout=900)
R.check("upstream_unit_tests_responses_adapters", code == 0, out)
R.write()
