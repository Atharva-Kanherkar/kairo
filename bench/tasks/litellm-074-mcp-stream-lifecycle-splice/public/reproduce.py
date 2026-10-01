import os
import tempfile

from kairo_verify import Upstream, show
from rigs import litellm

from upstream import make_responder, proxy_config, request_body

call_log = os.path.join(tempfile.mkdtemp(), "mcp-calls.jsonl")
with Upstream(make_responder()) as up, litellm.Proxy(config=proxy_config(up.url, call_log)) as proxy:
    status, headers, raw = proxy.post("/v1/responses", request_body("auto"))
    show(f"client received HTTP {status} (raw SSE)", raw.decode())
    show("MCP calls", open(call_log).read() if os.path.exists(call_log) else "(none)")

    from openai import OpenAI
    client = OpenAI(base_url=f"{proxy.url}/v1", api_key=litellm.MASTER_KEY, max_retries=0)
    kwargs = {k: v for k, v in request_body("auto").items() if k != "stream"}
    try:
        with client.responses.stream(**kwargs) as stream:
            for _ in stream:
                pass
            show("OpenAI SDK final response", stream.get_final_response().model_dump(mode="json"))
    except Exception as exc:
        show("OpenAI SDK raised", f"{type(exc).__name__}: {exc}")
