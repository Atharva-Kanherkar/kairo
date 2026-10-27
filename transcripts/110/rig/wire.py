"""Send one Anthropic /v1/messages request to a LiteLLM proxy, save raw client bytes,
and move the relay's forwarded exchange(s) for this request into <outdir>/upstream/.
usage: wire.py PORT ALIAS stream|plain stop|nostop OUTDIR RELAY_DIR"""
import glob, http.client, json, os, shutil, sys
port, alias, mode, trig, outdir, relay = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
os.makedirs(f"{outdir}/upstream", exist_ok=True)
for p in glob.glob(f"{relay}/fwd-*"): os.remove(p)
body = {"model": alias, "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Output exactly these three words separated by single spaces and nothing else: ALPHA STOPHERE OMEGA"}]}
if trig == "stop": body["stop_sequences"] = ["STOPHERE"]
if mode == "stream": body["stream"] = True
raw = json.dumps(body).encode()
hdr = {"Content-Type": "application/json", "x-api-key": "sk-test-master", "anthropic-version": "2023-06-01", "Content-Length": str(len(raw))}
open(f"{outdir}/client-request.http", "wb").write(f"POST /v1/messages HTTP/1.1\r\nHost: localhost:{port}\r\n".encode() + "".join(f"{k}: {v}\r\n" for k, v in hdr.items()).encode() + b"\r\n" + raw)
c = http.client.HTTPConnection("localhost", port, timeout=300); c.request("POST", "/v1/messages", raw, hdr); r = c.getresponse(); rb = r.read()
open(f"{outdir}/client-response.http", "wb").write(f"HTTP/1.1 {r.status} {r.reason}\r\n".encode() + "".join(f"{k}: {v}\r\n" for k, v in r.getheaders()).encode() + b"\r\n" + rb)
for p in sorted(glob.glob(f"{relay}/fwd-*")): shutil.move(p, f"{outdir}/upstream/{os.path.basename(p)}")
text, stop_reason, err = "", None, None
try:
    if r.status != 200: err = json.loads(rb)["error"]["message"]
    elif mode == "stream":
        for line in rb.decode().splitlines():
            if not line.startswith("data:"): continue
            ev = json.loads(line[5:])
            if ev.get("type") == "content_block_delta": text += ev["delta"].get("text", "")
            if ev.get("type") == "message_delta": stop_reason = ev["delta"].get("stop_reason")
            if ev.get("type") == "error": err = ev["error"].get("message")
    else:
        j = json.loads(rb); text = "".join(b.get("text", "") for b in j.get("content", [])); stop_reason = j.get("stop_reason")
except Exception as e: err = f"unparsed: {e}"
print(r.status, len(os.listdir(f"{outdir}/upstream")) // 2, stop_reason, json.dumps(text), (err or "")[:110].replace("\n", " "))
