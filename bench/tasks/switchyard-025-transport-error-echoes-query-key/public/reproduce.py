from kairo_verify import free_port, show
from rigs import switchyard

closed = free_port()  # nothing listens here
cfg = switchyard.passthrough(f"http://127.0.0.1:{closed}/v1?key=CANARY_QUERY_KEY", "openai_chat")
with switchyard.Gateway(cfg) as gw:
    status, body, raw = gw.post_json("/v1/chat/completions", {"model": "captured-model", "messages": [{"role": "user", "content": "hi"}]})
    show(f"client received HTTP {status}", raw)
