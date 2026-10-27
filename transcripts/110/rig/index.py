"""Build wire/results.json, direct/results.json and agents/langchain-react/results.json from the captures."""
import glob, json, os, re
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = {"g54m": "gpt-5.4-mini", "g55": "gpt-5.5", "g41m": "gpt-4.1-mini"}
def split(path):
    raw = open(path, "rb").read(); head, _, body = raw.partition(b"\r\n\r\n")
    return head.decode(), body
def status(path): return int(split(path)[0].split()[1])
rows = []
for d in sorted(glob.glob(f"{root}/wire/*/")):
    cell = os.path.basename(d.rstrip("/"))
    alias, mode, trig, build, run = re.match(r"(g54m|g55|g41m)-(plain|stream)-(stop|nostop)-(main|pre|fix)-r(\d)", cell).groups()
    fwd = sorted(glob.glob(f"{d}/upstream/*-request.http"))
    row = {"cell": cell, "model": MODELS[alias], "alias": alias, "stream": mode == "stream", "stop_sequences_sent": trig == "stop",
           "build": build, "run": int(run), "client_status": status(f"{d}/client-response.http"), "forwarded_requests": len(fwd)}
    if fwd:
        head, body = split(fwd[0]); fb = json.loads(body)
        row.update(forwarded_path=head.split()[1], forwarded_stop=fb.get("stop"),
                   provider_status=status(fwd[0].replace("-request", "-response")))
    rows.append(row)
json.dump(rows, open(f"{root}/wire/results.json", "w"), indent=1)
drows = []
for d in sorted(glob.glob(f"{root}/direct/*/")):
    cell = os.path.basename(d.rstrip("/"))
    api, model, trig, run = re.match(r"(chat|responses)-(.+)-(stop|nostop)-r(\d)", cell).groups()
    body = json.loads(split(f"{d}/response.http")[1])
    drows.append({"cell": cell, "api": api, "model": model, "stop_sent": trig == "stop", "run": int(run),
                  "status": status(f"{d}/response.http"), "error": (body.get("error") or {}).get("message")})
json.dump(drows, open(f"{root}/direct/results.json", "w"), indent=1)
arows = []
for d in sorted(glob.glob(f"{root}/agents/langchain-react/*/")):
    cell = os.path.basename(d.rstrip("/")); alias, build, run = re.match(r"(g54m|g41m)-(main|pre|fix)-r(\d)", cell).groups()
    res = json.load(open(f"{d}/result.json"))
    arows.append({"cell": cell, "model": MODELS[alias], "build": build, "run": int(run), "outcome": res["outcome"],
                  "llm_calls": len(glob.glob(f"{d}/client/*-request.http")), "tool_executions": res["tool_executions"],
                  "clean_single_execution": res["tool_executions"] == ["A-17"]})
json.dump(arows, open(f"{root}/agents/langchain-react/results.json", "w"), indent=1)
print(len(rows), len(drows), len(arows))
