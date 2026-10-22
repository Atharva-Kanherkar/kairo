import base64, json, os, sys, urllib.request
KEY = os.environ["GEMINI_API_KEY"]
img = base64.b64encode(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "code.png"),"rb").read()).decode()
def body(model, shape):
    fc = {"functionCall": {"name": "read_image", "args": {"path": "code.png"}}}
    if model.startswith(("gemini-3","gemini-4")): fc["thoughtSignature"] = "skip_thought_signature_validator"
    fr = {"name": "read_image", "response": {"content": ""}}
    if shape == "nested":
        fr["parts"] = [{"inlineData": {"mimeType": "image/png", "data": img}}]
        user_parts = [{"functionResponse": fr}]
    elif shape == "sibling":
        user_parts = [{"functionResponse": fr}, {"inlineData": {"mimeType": "image/png", "data": img}}]
    else:
        user_parts = [{"functionResponse": {"name":"read_image","response":{"content":"ZEBRA-7342 (text only)"}}}]
    return {"contents": [
        {"role": "user", "parts": [{"text": "Call read_image on code.png, then reply with exactly the text shown in the image."}]},
        {"role": "model", "parts": [fc]},
        {"role": "user", "parts": user_parts}],
      "tools": [{"functionDeclarations": [{"name": "read_image", "description": "Read an image file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]}]}
def call(model, shape):
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body(model, shape)).encode(), headers={"content-type": "application/json", "x-goog-api-key": KEY})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=120)); 
        txt = "".join(p.get("text","") for p in r["candidates"][0]["content"].get("parts",[]) if not p.get("thought"))
        return "200 " + txt.strip()[:60].replace("\n"," ")
    except urllib.error.HTTPError as e:
        return f"{e.code} " + json.loads(e.read()).get("error",{}).get("message","")[:90]
    except Exception as e:
        return "EXC " + repr(e)[:80]
    except urllib.error.HTTPError as e:
        return f"{e.code} " + json.loads(e.read()).get("error",{}).get("message","")[:90]
if __name__ == "__main__":
  for m in sys.argv[1].split(","):
      for s in sys.argv[2].split(","):
          print(f"{m:34s} {s:8s} {call(m, s)}", flush=True)
