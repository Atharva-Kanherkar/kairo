# Deterministic OpenAI-compatible upstream for finding 111.
#
# Purpose: isolate which model LiteLLM dispatches under a key allowlist, offline
# and with no provider keys. Every response echoes the model named in the
# forwarded request body, so the served model is visible in the bytes. Each
# forwarded request and response is written sequentially to SCRATCH so the driver
# can attach the exact forwarded bytes to the client call that produced them.
import sys, os, json, itertools, time
from aiohttp import web

PORT = int(sys.argv[1])
SCRATCH = sys.argv[2]
os.makedirs(SCRATCH, exist_ok=True)
counter = itertools.count(1)

SECRET_HEADERS = {"authorization", "x-api-key", "x-goog-api-key", "api-key"}


def _redact(headers):
    out = []
    for k, v in headers.items():
        out.append((k, "[REDACTED]" if k.lower() in SECRET_HEADERS else v))
    return out


async def handle(req):
    i = next(counter)
    body = await req.read()
    with open(os.path.join(SCRATCH, f"{i:04d}-request.http"), "wb") as f:
        f.write(f"{req.method} {req.path_qs} HTTP/1.1\n".encode())
        for k, v in _redact(req.headers):
            f.write(f"{k}: {v}\n".encode())
        f.write(b"\n")
        f.write(body)

    try:
        parsed = json.loads(body)
        served_model = parsed.get("model")
        wants_stream = bool(parsed.get("stream"))
    except Exception:
        served_model = None
        wants_stream = False

    path = req.path
    if wants_stream and "/images/" not in path:
        chunks = [
            {"id": f"chatcmpl-fake{i}", "object": "chat.completion.chunk", "model": served_model,
             "choices": [{"index": 0, "delta": {"role": "assistant",
                          "content": f"served-by-upstream-model={served_model}"}, "finish_reason": None}]},
            {"id": f"chatcmpl-fake{i}", "object": "chat.completion.chunk", "model": served_model,
             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000}},
        ]
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        raw = sse.encode()
        with open(os.path.join(SCRATCH, f"{i:04d}-response.http"), "wb") as f:
            f.write(b"HTTP/1.1 200 OK\ncontent-type: text/event-stream\n\n")
            f.write(raw)
        return web.Response(body=raw, content_type="text/event-stream")

    if "/images/" in path:
        payload = {
            "created": int(time.time()),
            "data": [{"b64_json": "", "revised_prompt": f"served-by-upstream-model={served_model}"}],
            "usage": {"total_tokens": 2000, "input_tokens": 1000, "output_tokens": 1000},
        }
    elif path.endswith("/responses") or "/responses" in path:
        payload = {
            "id": f"resp-fake{i}", "object": "response", "created_at": int(time.time()),
            "model": served_model, "status": "completed",
            "output": [{"type": "message", "role": "assistant", "id": f"msg-{i}",
                        "content": [{"type": "output_text", "text": f"served-by-upstream-model={served_model}"}]}],
            "usage": {"input_tokens": 1000, "output_tokens": 1000, "total_tokens": 2000},
        }
    else:
        payload = {
            "id": f"chatcmpl-fake{i}", "object": "chat.completion", "created": int(time.time()),
            "model": served_model,
            "choices": [{"index": 0, "message": {"role": "assistant",
                         "content": f"served-by-upstream-model={served_model}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
        }

    raw = json.dumps(payload).encode()
    with open(os.path.join(SCRATCH, f"{i:04d}-response.http"), "wb") as f:
        f.write(b"HTTP/1.1 200 OK\ncontent-type: application/json\n\n")
        f.write(raw)
    return web.Response(body=raw, content_type="application/json")


app = web.Application(client_max_size=64 * 1024 * 1024)
app.router.add_route("*", "/{path:.*}", handle)
web.run_app(app, host="127.0.0.1", port=PORT, print=None)
