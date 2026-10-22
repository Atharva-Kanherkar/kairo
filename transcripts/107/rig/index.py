"""Build wire/results.json, direct/results.json and agents/codex/results.txt from the captures."""
import glob, json, os, re
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = {"g25p": "gemini-2.5-pro", "g25fl": "gemini-2.5-flash-lite", "g38f": "gemini-3.8-flash"}
def status(path): return int(open(path, "rb").readline().split()[1])
rows = []
for d in sorted(glob.glob(f"{root}/wire/*/")):
    cell = os.path.basename(d.rstrip("/"))
    dialect, alias, build, run = re.match(r"(chat|messages|responses)-(g25p|g25fl|g38f)-(v1\.106\.2|v1\.88\.2|patched)-r(\d)", cell).groups()
    up = sorted(os.path.basename(p) for p in glob.glob(f"{d}/upstream/*-request.http"))
    resp = open(f"{d}/client-response.http", "rb").read().decode()
    rows.append({"cell": cell, "dialect": dialect, "model": MODELS[alias], "alias": alias, "build": build, "run": int(run),
                 "client_status": status(f"{d}/client-response.http"),
                 "forwarded_request": f"upstream/{up[0]}", "forwarded_response": f"upstream/{up[0].replace('-request', '-response')}",
                 "provider_status": status(f"{d}/upstream/{up[0].replace('-request', '-response')}"),
                 "image_text_in_reply": "ZEBRA-7342" in resp})
json.dump(rows, open(f"{root}/wire/results.json", "w"), indent=1)
drows = []
for d in sorted(glob.glob(f"{root}/direct/*/")):
    cell = os.path.basename(d.rstrip("/"))
    model, shape, run = re.match(r"(.+)-(nested|sibling)-r(\d)", cell).groups()
    drows.append({"cell": cell, "model": model, "shape": shape, "run": int(run), "status": status(f"{d}/response.http"),
                  "image_text_in_reply": "ZEBRA-7342" in open(f"{d}/response.http", "rb").read().decode()})
json.dump(drows, open(f"{root}/direct/results.json", "w"), indent=1)
with open(f"{root}/agents/codex/results.txt", "w") as f:
    for d in sorted(glob.glob(f"{root}/agents/codex/*/")):
        out = open(f"{d}/out.txt").read()
        f.write(f"{os.path.basename(d.rstrip('/'))} reconnects={out.count('Reconnecting...')} "
                f"multimodal_400={'Multimodal function responses are not supported' in out} "
                f"answer={open(f'{d}/answer.txt').read().strip() if os.path.exists(f'{d}/answer.txt') else '-'}\n")
print(len(rows), len(drows))
