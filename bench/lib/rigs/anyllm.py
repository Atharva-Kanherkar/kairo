"""Drive any-llm's Anthropic Messages bridge against a local OpenAI-compatible upstream.

``call(**messages_kwargs)`` runs ``any_llm.messages(provider="openai", ...)`` with
``api_base`` pointed at a fresh :class:`kairo_verify.Upstream` and returns the
client-side result (or exception) plus every request the upstream received.
"""

from __future__ import annotations

from typing import Any, Optional

from kairo_verify import Reply, Upstream

MODEL = "mock-model"

CHAT_OK = {
    "id": "chatcmpl-kairo",
    "object": "chat.completion",
    "created": 1767225600,
    "model": MODEL,
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
}


class Outcome:
    def __init__(self, response: Any, error: Optional[BaseException], requests: list) -> None:
        self.response = response
        self.error = error
        self.requests = requests

    @property
    def forwarded(self) -> Optional[dict]:
        """The last JSON body the upstream received, or None."""
        for cap in reversed(self.requests):
            if isinstance(cap.json, dict):
                return cap.json
        return None


def call(reply: Optional[dict] = None, **kwargs: Any) -> Outcome:
    from any_llm import messages

    body = reply or CHAT_OK
    with Upstream(lambda cap: Reply.json(body)) as up:
        kwargs.setdefault("max_tokens", 64)
        try:
            resp = messages(model=MODEL, provider="openai", api_base=f"{up.url}/v1", api_key="sk-kairo-test", **kwargs)
            err = None
        except Exception as exc:  # the verifier decides whether an error is acceptable
            resp, err = None, exc
        return Outcome(resp, err, list(up.requests))
