"""Deterministic OpenAI Responses upstream that records what Bifrost forwards."""

from kairo_verify import Reply

RESPONSE = {"id": "resp_kairo", "object": "response", "created_at": 0, "model": "gpt-4o", "status": "completed",
            "output": [{"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok", "annotations": []}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}


def respond(cap):
    if cap.method == "GET":
        return Reply.json({"object": "list", "data": [{"id": "gpt-4o", "object": "model"}]})
    return Reply.json(RESPONSE)
