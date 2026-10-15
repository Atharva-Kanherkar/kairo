"""Minimal coding-agent loop: run write_file tool calls until the model stops calling tools."""
import json, os, shutil, sys, anthropic, openai
WANT = '<tool_call>\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n</tool_call>'
OTOOLS = [{"type":"function","function":{"name":"write_file","description":"Write a file","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}}}]
ATOOLS = [{"name":"write_file","description":"Write a file","input_schema":OTOOLS[0]["function"]["parameters"]}]
def write(ws, a):
    p = os.path.join(ws, a["path"]); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(a["content"]); return "ok"
def run_openai(script, ws, stream):
    c = openai.OpenAI(base_url="http://localhost:8000/v1", api_key="x")
    msgs = [{"role":"user","content":f"SCRIPT:{script} write the fixture"}]; calls = 0
    for turn in range(3):
        if stream:
            r = c.chat.completions.create(model="Qwen/Qwen3-0.6B", messages=msgs, tools=OTOOLS, max_tokens=400)
        else:
            r = c.chat.completions.create(model="Qwen/Qwen3-0.6B", messages=msgs, tools=OTOOLS, max_tokens=400)
        m = r.choices[0].message
        if not m.tool_calls: return calls, r.choices[0].finish_reason, m.content
        for tc in m.tool_calls:
            calls += 1; write(ws, json.loads(tc.function.arguments))
        return calls, r.choices[0].finish_reason, m.content  # one turn is enough to judge
def run_anthropic(script, ws, stream):
    c = anthropic.Anthropic(base_url="http://localhost:8000", api_key="x")
    kw = dict(model="Qwen/Qwen3-0.6B", max_tokens=400, tools=ATOOLS, messages=[{"role":"user","content":f"SCRIPT:{script} write the fixture"}])
    if stream:
        with c.messages.stream(**kw) as s: r = s.get_final_message()
    else:
        r = c.messages.create(**kw)
    calls = 0
    for b in r.content:
        if b.type == "tool_use": calls += 1; write(ws, b.input)
    text = "".join(b.text for b in r.content if b.type == "text")
    return calls, r.stop_reason, text
for script in ("hcl_real", "hcl_ctrl"):
    for sdk, fn, stream in (("openai", run_openai, False), ("anthropic", run_anthropic, False), ("anthropic", run_anthropic, True)):
        for i in range(int(sys.argv[1])):
            ws = f"/tmp/ws-{script}-{sdk}-{stream}-{i}"; shutil.rmtree(ws, ignore_errors=True); os.makedirs(ws)
            calls, stop, text = fn(script, ws, stream)
            f = os.path.join(ws, "tests/fixtures/hermes_sample.txt")
            got = open(f).read() if os.path.exists(f) else None
            print(json.dumps({"script":script,"sdk":sdk,"stream":stream,"run":i+1,"tool_runs":calls,"stop":stop,"assistant_text":text,"file_written":got is not None,"file_exact":got == WANT}))
