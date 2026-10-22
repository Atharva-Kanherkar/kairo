"""Adversarial control for the checker: the v1.88.2 forwarded turn with the image
part removed and its data URL pasted as text into functionResponse.response, the
#23712 failure shape. Sent straight to Gemini; raw bytes saved, key redacted."""
import http.client, json, os, sys
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wire", "messages-g25p-v1.88.2-r1", "upstream")
KEY = os.environ["GEMINI_API_KEY"]
out, model = sys.argv[1], sys.argv[2]
raw = open(os.path.join(SRC, sorted(f for f in os.listdir(SRC) if f.endswith("-request.http"))[0]), "rb").read()
body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
turn = body["contents"][-1]
image = next(p for p in turn["parts"] if "inline_data" in p)["inline_data"]
turn["parts"] = [p for p in turn["parts"] if "inline_data" not in p]
turn["parts"][0]["function_response"]["response"]["content"] = f"data:{image['mime_type']};base64,{image['data']}"
for r in (1, 2, 3):
    d = f"{out}/text-data-url-{model}-r{r}"; os.makedirs(d, exist_ok=True)
    data = json.dumps(body).encode(); path = f"/v1beta/models/{model}:generateContent"
    hdr = {"Content-Type": "application/json", "x-goog-api-key": KEY, "Content-Length": str(len(data))}
    open(f"{d}/request.http", "wb").write(f"POST {path} HTTP/1.1\r\nHost: generativelanguage.googleapis.com\r\n".encode()
        + "".join(f"{k}: {'[REDACTED]' if k == 'x-goog-api-key' else v}\r\n" for k, v in hdr.items()).encode() + b"\r\n" + data)
    c = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=300); c.request("POST", path, data, hdr)
    resp = c.getresponse(); rb = resp.read()
    open(f"{d}/response.http", "wb").write(f"HTTP/1.1 {resp.status} {resp.reason}\r\n".encode()
        + "".join(f"{k}: {v}\r\n" for k, v in resp.getheaders()).encode() + b"\r\n" + rb)
    j = json.loads(rb)
    txt = j["error"]["message"] if "error" in j else "".join(p.get("text", "") for p in j["candidates"][0]["content"].get("parts", []) if not p.get("thought"))
    print(f"r{r} {resp.status} zebra={'ZEBRA-7342' in txt} {txt[:90]!r}", flush=True)
