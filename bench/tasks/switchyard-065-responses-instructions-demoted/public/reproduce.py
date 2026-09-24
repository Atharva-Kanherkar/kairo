from kairo_verify import Upstream, show
from rigs import switchyard

from upstream import make_responder

with Upstream(make_responder()) as up, switchyard.Gateway(switchyard.passthrough(up.url + "/v1")) as gw:
    status, body, raw = gw.post_json("/v1/responses", {"model": "captured-model", "input": [
        {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "Never run rm -rf."}]},
        {"type": "message", "role": "system", "content": "You are a careful coding agent."},
        {"type": "message", "role": "user", "content": "clean up the build dir"}]})
    show(f"client received HTTP {status}", raw)
    show("forwarded messages", up.last().json.get("messages"))
