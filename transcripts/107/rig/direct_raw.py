"""Direct Gemini API control, raw bytes saved (x-goog-api-key redacted)."""
import json, os, sys, http.client
sys.path.insert(0, os.path.dirname(__file__))
from direct import body, KEY
out = sys.argv[3]
for m in sys.argv[1].split(","):
    for s in sys.argv[2].split(","):
        for r in (1, 2, 3):
            d = f"{out}/{m}-{s}-r{r}"; os.makedirs(d, exist_ok=True)
            raw = json.dumps(body(m, s)).encode(); path = f"/v1beta/models/{m}:generateContent"
            hdr = {"Content-Type": "application/json", "x-goog-api-key": KEY, "Content-Length": str(len(raw))}
            open(f"{d}/request.http", "wb").write(f"POST {path} HTTP/1.1\r\nHost: generativelanguage.googleapis.com\r\n".encode()
                + "".join(f"{k}: {'[REDACTED]' if k == 'x-goog-api-key' else v}\r\n" for k, v in hdr.items()).encode() + b"\r\n" + raw)
            c = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=180); c.request("POST", path, raw, hdr)
            resp = c.getresponse(); rb = resp.read()
            open(f"{d}/response.http", "wb").write(f"HTTP/1.1 {resp.status} {resp.reason}\r\n".encode()
                + "".join(f"{k}: {v}\r\n" for k, v in resp.getheaders()).encode() + b"\r\n" + rb)
            j = json.loads(rb)
            txt = j["error"]["message"][:70] if "error" in j else "".join(p.get("text", "") for p in j["candidates"][0]["content"].get("parts", []) if not p.get("thought"))[:40]
            print(f"{m:24s} {s:8s} r{r} {resp.status} {'ZEBRA-7342' in txt} {txt!r}", flush=True)
