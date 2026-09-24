"""Logging HTTP forwarder. Records every request and response, redacting credentials.

    python tap.py --listen 9997 --target https://api.openai.com --out $RIG/runs/upstream
"""
import argparse, itertools, json, pathlib, time
import httpx, uvicorn
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

p = argparse.ArgumentParser()
p.add_argument("--listen", type=int, required=True)
p.add_argument("--target", required=True)
p.add_argument("--out", required=True)
p.add_argument("--inject-key-env")
a = p.parse_args()
import os
INJECT = os.environ.get(a.inject_key_env) if a.inject_key_env else None
OUT = pathlib.Path(a.out)
counter = itertools.count(1)
SENSITIVE = {"authorization", "api-key", "x-api-key", "openai-api-key", "cookie", "chatgpt-account-id"}
HOP = {"host", "content-length", "connection", "transfer-encoding"}
client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))


def clean(headers):
    return {k: ("[REDACTED]" if k.lower() in SENSITIVE else v) for k, v in headers.items()}


def outdir():
    # Resolve at call time so the driver can switch run directories.
    cur = pathlib.Path(a.out)
    marker = cur.parent / "CURRENT"
    if marker.exists():
        cur = pathlib.Path(marker.read_text().strip()) / cur.name
    cur.mkdir(parents=True, exist_ok=True)
    return cur


async def handle(request):
    n = next(counter)
    d = outdir()
    body = await request.body()
    path = request.url.path + (("?" + request.url.query) if request.url.query else "")
    rec = {"n": n, "t": time.time(), "method": request.method, "path": path, "headers": clean(dict(request.headers))}
    (d / f"{n:03d}-request.json").write_text(json.dumps(rec, indent=2))
    (d / f"{n:03d}-request-body.bin").write_bytes(body)
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    if not any(k.lower() == "accept-encoding" for k in fwd_headers):
        fwd_headers["Accept-Encoding"] = "identity"
    if INJECT:
        fwd_headers = {k: v for k, v in fwd_headers.items() if k.lower() != "authorization"}
        fwd_headers["Authorization"] = "Bearer " + INJECT
    req = client.build_request(request.method, a.target + path, headers=fwd_headers, content=body)
    resp = await client.send(req, stream=True)
    meta = {"n": n, "status": resp.status_code, "headers": clean(dict(resp.headers))}
    (d / f"{n:03d}-response.json").write_text(json.dumps(meta, indent=2))
    sink = open(d / f"{n:03d}-response-body.bin", "wb")

    async def gen():
        try:
            async for chunk in resp.aiter_raw():
                sink.write(chunk)
                yield chunk
        finally:
            sink.close()
            await resp.aclose()

    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
    return StreamingResponse(gen(), status_code=resp.status_code, headers=out_headers)


app = Starlette(routes=[Route("/{path:path}", handle, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])])
uvicorn.run(app, host="127.0.0.1", port=a.listen, log_level="warning")
