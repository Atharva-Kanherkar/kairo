# Deterministic Google AI Studio-compatible upstream for finding 113.
#
# Purpose: isolate which model LiteLLM dispatches under a key allowlist, offline
# and with no provider keys. Every response echoes the model named in the
# forwarded request URL, so the served model is visible in the bytes. Each
# forwarded request and response is written sequentially to SCRATCH so the driver
# can attach the exact forwarded bytes to the client call that produced them.
import sys, os, json, itertools, re, time
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


def _extract_model_from_path(path):
    m = re.search(r"/models/([^:]+):", path)
    if m:
        return m.group(1)
    return None


async def handle(req):
    i = next(counter)
    body = await req.read()
    with open(os.path.join(SCRATCH, f"{i:04d}-request.http"), "wb") as f:
        f.write(f"{req.method} {req.path_qs} HTTP/1.1\n".encode())
        for k, v in _redact(req.headers):
            f.write(f"{k}: {v}\n".encode())
        f.write(b"\n")
        f.write(body)

    served_model = _extract_model_from_path(req.path)
    wants_stream = "stream" in req.path or "alt=sse" in req.query_string

    if wants_stream:
        chunk = {
            "candidates": [{
                "content": {"parts": [{"text": f"served-by-upstream-model={served_model}"}], "role": "model"},
                "finishReason": "STOP",
                "index": 0
            }],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 10, "totalTokenCount": 20},
            "modelVersion": served_model or "unknown"
        }
        sse = f"data: {json.dumps(chunk)}\n\n"
        raw = sse.encode()
        with open(os.path.join(SCRATCH, f"{i:04d}-response.http"), "wb") as f:
            f.write(b"HTTP/1.1 200 OK\ncontent-type: text/event-stream\n\n")
            f.write(raw)
        return web.Response(body=raw, content_type="text/event-stream")

    payload = {
        "candidates": [{
            "content": {"parts": [{"text": f"served-by-upstream-model={served_model}"}], "role": "model"},
            "finishReason": "STOP",
            "index": 0
        }],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 10, "totalTokenCount": 20},
        "modelVersion": served_model or "unknown"
    }

    raw = json.dumps(payload).encode()
    with open(os.path.join(SCRATCH, f"{i:04d}-response.http"), "wb") as f:
        f.write(b"HTTP/1.1 200 OK\ncontent-type: application/json\n\n")
        f.write(raw)
    return web.Response(body=raw, content_type="application/json")


app = web.Application(client_max_size=64 * 1024 * 1024)
app.router.add_route("*", "/{path:.*}", handle)
web.run_app(app, host="127.0.0.1", port=PORT, print=None)
