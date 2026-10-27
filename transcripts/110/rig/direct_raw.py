"""Direct OpenAI control, no gateway. Saves raw request/response bytes; the credential
and account-metadata header values are replaced with [REDACTED].
usage: direct_raw.py chat|responses MODEL stop|nostop OUTDIR  (needs OPENAI_API_KEY)"""
import http.client, json, os, sys
api, model, trig, outdir = sys.argv[1:5]; os.makedirs(outdir, exist_ok=True)
prompt = "Output exactly these three words separated by single spaces and nothing else: ALPHA STOPHERE OMEGA"
if api == "chat":
    path, body = "/v1/chat/completions", {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if trig == "stop": body["stop"] = ["STOPHERE"]
else:
    path, body = "/v1/responses", {"model": model, "input": prompt, "store": False}
    if trig == "stop": body["stop"] = ["STOPHERE"]
raw = json.dumps(body).encode()
hdr = {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Length": str(len(raw))}
SECRET = {"authorization", "openai-organization", "openai-project", "set-cookie", "cf-ray"}
def h(items): return "".join(f"{k}: {'[REDACTED]' if k.lower() in SECRET or k.lower().startswith('x-ratelimit-') else v}\r\n" for k, v in items)
open(f"{outdir}/request.http", "wb").write(f"POST {path} HTTP/1.1\r\nHost: api.openai.com\r\n{h(hdr.items())}\r\n".encode() + raw)
c = http.client.HTTPSConnection("api.openai.com", timeout=300); c.request("POST", path, raw, hdr); r = c.getresponse(); rb = r.read()
open(f"{outdir}/response.http", "wb").write(f"HTTP/1.1 {r.status} {r.reason}\r\n{h(r.getheaders())}\r\n".encode() + rb)
j = json.loads(rb)
if r.status != 200: print(r.status, j["error"]["message"])
elif api == "chat": print(r.status, json.dumps(j["choices"][0]["message"]["content"]), j["choices"][0]["finish_reason"])
else: print(r.status, json.dumps("".join(c.get("text", "") for o in j["output"] for c in (o.get("content") or []))))
