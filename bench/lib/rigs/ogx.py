"""Run the real OGX server (`ogx go`) against a local OpenAI-compatible upstream.

    with ogx.Rig() as rig:
        status, body, raw, forwarded = rig.messages({...})

``forwarded`` lists the JSON bodies OGX sent to the upstream's
/v1/chat/completions for that one client request.
"""

from __future__ import annotations

import json

from kairo_verify import Reply, Service, Upstream, free_port, request, wait_http

MODEL = "openai/mock-gpt"


def _chat(body: dict) -> Reply:
    text = "Synthetic control response."
    if body.get("stream"):
        base = {"id": "chatcmpl-kairo", "object": "chat.completion.chunk", "created": 0, "model": "mock-gpt"}
        return Reply.sse([
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 4}},
        ], done=True)
    return Reply.json({
        "id": "chatcmpl-kairo", "object": "chat.completion", "created": 0, "model": body.get("model", "mock-gpt"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 4},
    })


def _respond(cap) -> Reply:
    if cap.path.rstrip("/").endswith("/models"):
        return Reply.json({"object": "list", "data": [{"id": "mock-gpt", "object": "model", "owned_by": "kairo"}]})
    if cap.path.rstrip("/").endswith("/chat/completions"):
        return _chat(cap.json or {})
    return Reply.json({"error": {"message": f"unexpected path {cap.path}"}}, 404)


class Rig:
    def __init__(self, log_path: str = "/tmp/ogx-server.log") -> None:
        self.log_path = log_path

    def __enter__(self) -> "Rig":
        self.upstream = Upstream(_respond).__enter__()
        self.port = free_port()
        self.service = Service(
            ["ogx", "go", "--insecure", "--no-auth", "--port", str(self.port)],
            env={"OPENAI_BASE_URL": f"{self.upstream.url}/v1", "OPENAI_API_KEY": "sk-kairo-test"},
            cwd="/work/repo", log_path=self.log_path,
        ).__enter__()
        if not wait_http(f"http://127.0.0.1:{self.port}/v1/models", timeout=180, proc=self.service.proc):
            self.__exit__(None, None, None)
            raise RuntimeError("OGX did not start:\n" + self.service.log_tail())
        return self

    def __exit__(self, *exc) -> None:
        self.service.__exit__(*exc)
        self.upstream.__exit__(*exc)

    def messages(self, body: dict):
        before = len(self.upstream.requests)
        status, _, raw = request("POST", f"http://127.0.0.1:{self.port}/v1/messages", body, timeout=120)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        forwarded = [c.json for c in self.upstream.requests[before:] if c.path.rstrip("/").endswith("/chat/completions")]
        return status, parsed, raw, forwarded
