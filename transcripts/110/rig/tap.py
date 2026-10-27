"""Client-side tap: agent -> here -> LiteLLM proxy. Saves each raw request and response.
usage: tap.py PORT UPSTREAM_BASE OUTDIR. x-api-key and authorization values are redacted."""
import sys, os, json, itertools, aiohttp
from aiohttp import web
UP = sys.argv[2]; OUT = sys.argv[3]; os.makedirs(OUT, exist_ok=True)
n = itertools.count(1)
HOP = {"host","content-length","transfer-encoding","connection","accept-encoding"}
def red(h): return {k: ("[REDACTED]" if k.lower() in ("authorization","x-api-key") else v) for k,v in h.items()}
async def h(req):
    i = next(n); body = await req.read()
    with open(f"{OUT}/{i:03d}-request.http","wb") as f:
        f.write(f"{req.method} {req.path_qs} HTTP/1.1\n".encode())
        for k,v in red(req.headers).items(): f.write(f"{k}: {v}\n".encode())
        f.write(b"\n"+body)
    async with aiohttp.ClientSession(auto_decompress=True) as s:
        async with s.request(req.method, UP+req.path_qs, data=body, headers={k:v for k,v in req.headers.items() if k.lower() not in HOP}) as r:
            resp = web.StreamResponse(status=r.status, headers={k:v for k,v in r.headers.items() if k.lower() not in HOP|{"content-encoding"}})
            await resp.prepare(req)
            with open(f"{OUT}/{i:03d}-response.http","wb") as f:
                f.write(f"HTTP/1.1 {r.status}\n".encode())
                for k,v in r.headers.items(): f.write(f"{k}: {v}\n".encode())
                f.write(b"\n")
                async for chunk in r.content.iter_any():
                    f.write(chunk); await resp.write(chunk)
            await resp.write_eof(); return resp
app = web.Application(client_max_size=64*1024*1024); app.router.add_route("*","/{p:.*}",h)
web.run_app(app, host="127.0.0.1", port=int(sys.argv[1]), print=None)
