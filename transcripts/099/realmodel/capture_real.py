"""Record raw Qwen3 completions from OpenRouter's /api/v1/completions with the
Qwen chat template applied by hand, so no gateway parser touches the output.
The Authorization header is replaced with a marked redaction before saving.
Reads the key from the OPENROUTER_API_KEY line of a dotenv file; never prints it.

usage: capture_real.py <dotenv> <outdir> <runs> <prompt-kind: verbatim|edit>
"""
import http.client, json, os, sys

dotenv, out, runs, kind = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
key = [l.split("=", 1)[1].strip().strip('"') for l in open(dotenv) if l.startswith("OPENROUTER_API_KEY=")][0]
MODEL = "qwen/qwen3-235b-a22b-2507"
WF = {"type": "function", "function": {"name": "write_file", "description": "Write a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}
RF = {"type": "function", "function": {"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
tools = [WF] if kind == "verbatim" else [RF, WF]
sys_msg = ("You are a coding agent." if kind == "verbatim" else "You are a coding agent working in a git repository.") + (
    "\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
    "You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n" + "\n".join(json.dumps(t) for t in tools) + "\n</tools>\n\n"
    "For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n"
    "<tool_call>\n{\"name\": <function-name>, \"arguments\": <args-json-object>}\n</tool_call>")
if kind == "verbatim":
    user = ("Create tests/fixtures/hermes_sample.txt containing exactly this two-line sample model output, verbatim:\n"
            "<tool_call>\n{\"name\": \"get_weather\", \"arguments\": {\"city\": \"Paris\"}}\n</tool_call>")
    prompt = f"<|im_start|>system\n{sys_msg}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n"
else:
    fixture = 'Model output used by test_parse_single_call:\n<tool_call>\n{"name": "get_weather", "arguments": {"city": "Paris", "unit": "celsius"}}\n</tool_call>\n'
    call1 = '<tool_call>\n{"name": "read_file", "arguments": {"path": "tests/fixtures/single_call.txt"}}\n</tool_call>'
    prompt = (f"<|im_start|>system\n{sys_msg}<|im_end|>\n<|im_start|>user\nThe test now expects Berlin. Update tests/fixtures/single_call.txt so the city is Berlin.<|im_end|>\n"
              f"<|im_start|>assistant\n{call1}<|im_end|>\n<|im_start|>user\n<tool_response>\n{fixture}</tool_response><|im_end|>\n<|im_start|>assistant\n")
for i in range(1, runs + 1):
    body = json.dumps({"model": MODEL, "prompt": prompt, "max_tokens": 400, "temperature": 0.7}, separators=(",", ":"))
    c = http.client.HTTPSConnection("openrouter.ai", timeout=180)
    c.request("POST", "/api/v1/completions", body, {"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    r = c.getresponse(); data = r.read()
    cell = os.path.join(out, f"{kind}-{i:02d}"); os.makedirs(cell, exist_ok=True)
    open(os.path.join(cell, "request.http"), "w").write(
        f"POST /api/v1/completions HTTP/1.1\r\nHost: openrouter.ai\r\nAuthorization: Bearer [REDACTED: OPENROUTER_API_KEY]\r\nContent-Type: application/json\r\nContent-Length: {len(body.encode())}\r\n\r\n{body}")
    keep = [h for h in r.getheaders() if h[0].lower() in ("content-type", "date")]
    open(os.path.join(cell, "response.http"), "wb").write(
        f"HTTP/1.1 {r.status} {r.reason}\r\n".encode() + b"".join(f"{k}: {v}\r\n".encode() for k, v in keep) + b"\r\n" + data)
    try:
        o = json.loads(data); text = o["choices"][0].get("text", ""); prov = o.get("provider")
    except Exception:
        text, prov = "", "ERR"
    open(os.path.join(cell, "completion.txt"), "w").write(text)
    print(cell, r.status, prov)
