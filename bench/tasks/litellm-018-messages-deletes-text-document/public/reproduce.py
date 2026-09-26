from kairo_verify import Upstream, show
from rigs import litellm

from upstream import proxy_config, respond

with Upstream(respond) as up, litellm.Proxy(config=proxy_config(up.url)) as proxy:
    status, body, raw = proxy.post_json("/v1/messages", {"model": "mock", "max_tokens": 64, "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "Summarize the attached incident report."},
            {"type": "document", "source": {"type": "text", "media_type": "text/plain",
                                            "data": "At 09:12 UTC the primary database failed over."}}]}]})
    show(f"client received HTTP {status}", raw)
    cap = up.last()
    show(f"forwarded to upstream {cap.path}", cap.json)
