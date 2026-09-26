"""A deterministic stand-in for OpenAI's /v1/alpha/search (and any other POST)."""

from kairo_verify import Reply


def respond(cap):
    body = cap.json or {}
    if "id" not in body:
        return Reply.json({"error": {"message": "Missing required parameter: 'id'.", "type": "invalid_request_error",
                                     "param": "id", "code": "missing_required_parameter"}}, 400)
    return Reply.json({"id": body["id"], "object": "search.result", "results": [{"title": "ok", "url": "https://example.com"}]})
