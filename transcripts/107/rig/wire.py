"""Send one request to a LiteLLM proxy, save raw client request/response bytes."""
import base64, http.client, json, sys, os
port, dialect, model, outdir = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
os.makedirs(outdir, exist_ok=True)
img = base64.b64encode(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "code.png"), "rb").read()).decode()
tool_desc = "Read an image file from disk"
if dialect == "chat":
    path = "/v1/chat/completions"
    body = {"model": model, "messages": [
        {"role": "user", "content": "Call read_image on code.png, then reply with exactly the text shown in the image."},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_image", "arguments": "{\"path\": \"code.png\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": [{"type": "text", "text": "Image read."}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}]}],
      "tools": [{"type": "function", "function": {"name": "read_image", "description": tool_desc, "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
elif dialect == "responses":
    path = "/v1/responses"
    body = {"model": model, "store": False, "input": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Call read_image on code.png, then reply with exactly the text shown in the image."}]},
        {"type": "function_call", "call_id": "call_1", "name": "read_image", "arguments": "{\"path\": \"code.png\"}"},
        {"type": "function_call_output", "call_id": "call_1", "output": [{"type": "input_image", "image_url": f"data:image/png;base64,{img}"}]}],
      "tools": [{"type": "function", "name": "read_image", "description": tool_desc, "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]}
else:
    path = "/v1/messages"
    body = {"model": model, "max_tokens": 512, "messages": [
        {"role": "user", "content": "Call read_image on code.png, then reply with exactly the text shown in the image."},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "read_image", "input": {"path": "code.png"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}}]}]}],
      "tools": [{"name": "read_image", "description": tool_desc, "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]}
raw = json.dumps(body).encode()
hdr = {"Content-Type": "application/json", "Authorization": "Bearer sk-test-master", "Content-Length": str(len(raw))}
open(f"{outdir}/client-request.http", "wb").write(f"POST {path} HTTP/1.1\r\nHost: localhost:{port}\r\n".encode() + "".join(f"{k}: {v}\r\n" for k, v in hdr.items()).encode() + b"\r\n" + raw)
c = http.client.HTTPConnection("localhost", port, timeout=300); c.request("POST", path, raw, hdr); r = c.getresponse(); rb = r.read()
open(f"{outdir}/client-response.http", "wb").write(f"HTTP/1.1 {r.status} {r.reason}\r\n".encode() + "".join(f"{k}: {v}\r\n" for k, v in r.getheaders()).encode() + b"\r\n" + rb)
try:
    j = json.loads(rb)
    txt = j.get("error", {}).get("message", "")[:120] if j.get("error") else (j["choices"][0]["message"].get("content") if dialect == "chat" else "".join(c.get("text", "") for o in j.get("output", []) for c in (o.get("content") or []) if isinstance(c, dict)) if dialect == "responses" else "".join(b.get("text", "") for b in j.get("content", [])))
except Exception: txt = rb[:120]
print(r.status, "ZEBRA-7342" in str(txt), str(txt).replace("\n", " ")[:120])
