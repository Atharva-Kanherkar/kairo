# Driver for finding 111. Runs the control ladder N times per cell against a
# running LiteLLM proxy, saving the client request/response bytes and the exact
# forwarded upstream bytes (matched from the fake upstream's scratch dir) for
# each run, then writes a results matrix.
#
# Usage: drive.py <proxy_base> <key> <scratch_dir> <out_dir> <n> [label]
import sys, os, json, glob, shutil, time
import urllib.request

PROXY, KEY, SCRATCH, OUT, N = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
LABEL = sys.argv[6] if len(sys.argv) > 6 else "main"
os.makedirs(OUT, exist_ok=True)

MSG_BODY = {"messages": [{"role": "user", "content": "hi"}]}
GC_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}

# cell: (route, auth_header, body_dict, description)
CELLS = [
    ("chat-body-denied", "POST", "/v1/chat/completions", "bearer",
     {"model": "premium-model", **MSG_BODY}, "chat, body=denied (documented 403 control)"),
    ("chat-query-denied-body-allowed", "POST", "/v1/chat/completions?model=premium-model", "bearer",
     {"model": "cheap-model", **MSG_BODY}, "chat ?model=denied body=allowed (sibling, PR #41852 covers this)"),
    ("deployments-chat-path-denied-body-allowed", "POST", "/openai/deployments/premium-model/chat/completions", "bearer",
     {"model": "cheap-model", **MSG_BODY}, "deployments chat path=denied body=allowed (sibling, PR #41852 covers this)"),
    ("gc-path-denied-body-allowed", "POST", "/v1beta/models/premium-model:generateContent", "goog",
     {"model": "cheap-model", **GC_BODY}, "generateContent, path=denied body=allowed (TRIGGER)"),
    ("gcstream-path-denied-body-allowed", "POST", "/v1beta/models/premium-model:streamGenerateContent", "goog",
     {"model": "cheap-model", **GC_BODY}, "streamGenerateContent, path=denied body=allowed (TRIGGER)"),
    ("images-path-denied-body-allowed", "POST", "/openai/deployments/premium-model/images/generations", "bearer",
     {"model": "cheap-model", "prompt": "a cat"}, "images deployment, path=denied body=allowed (TRIGGER)"),
    ("gc-trigger-removed", "POST", "/v1beta/models/cheap-model:generateContent", "goog",
     {"model": "cheap-model", **GC_BODY}, "generateContent, path=allowed body=allowed (trigger removed)"),
    ("messages-query-denied-body-allowed", "POST", "/v1/messages?model=premium-model", "bearer",
     {"model": "cheap-model", "max_tokens": 16, **MSG_BODY}, "messages ?model=denied body=allowed (negative control)"),
    ("messages-body-denied", "POST", "/v1/messages", "bearer",
     {"model": "premium-model", "max_tokens": 16, **MSG_BODY}, "messages body=denied (negative control, 403)"),
]


def scratch_indices():
    return {int(os.path.basename(p)[:4]) for p in glob.glob(os.path.join(SCRATCH, "*-request.http"))}


def forwarded_model(idx):
    try:
        with open(os.path.join(SCRATCH, f"{idx:04d}-request.http"), "rb") as f:
            raw = f.read()
        body = raw.split(b"\n\n", 1)[1]
        return json.loads(body).get("model")
    except Exception:
        return None


def call(route, method, headers, body):
    url = PROXY + route
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


results = []
for cell_id, method, route, auth, body, desc in CELLS:
    headers = {"Authorization": f"Bearer {KEY}"} if auth == "bearer" else {"x-goog-api-key": KEY}
    runs = []
    for r in range(1, N + 1):
        before = scratch_indices()
        status, resp_headers, resp_body = call(route, method, headers, body)
        time.sleep(0.2)
        after = scratch_indices()
        new = sorted(after - before)
        fwd_idx = new[-1] if new else None
        fwd_model = forwarded_model(fwd_idx) if fwd_idx is not None else None

        run_dir = os.path.join(OUT, cell_id, f"run-{r:02d}")
        os.makedirs(os.path.join(run_dir, "upstream"), exist_ok=True)
        redacted_headers = {k: ("[REDACTED]" if k.lower() in ("authorization", "x-goog-api-key") else v)
                            for k, v in headers.items()}
        with open(os.path.join(run_dir, "client-request.http"), "wb") as f:
            f.write(f"{method} {route} HTTP/1.1\n".encode())
            for k, v in redacted_headers.items():
                f.write(f"{k}: {v}\n".encode())
            f.write(b"content-type: application/json\n\n")
            f.write(json.dumps(body).encode())
        with open(os.path.join(run_dir, "client-response.http"), "wb") as f:
            f.write(f"HTTP/1.1 {status}\n".encode())
            f.write(b"\n")
            f.write(resp_body)
        if fwd_idx is not None:
            shutil.copy(os.path.join(SCRATCH, f"{fwd_idx:04d}-request.http"),
                        os.path.join(run_dir, "upstream", "fwd-request.http"))
            shutil.copy(os.path.join(SCRATCH, f"{fwd_idx:04d}-response.http"),
                        os.path.join(run_dir, "upstream", "fwd-response.http"))
        runs.append({"run": r, "status": status, "forwarded_model": fwd_model})
    statuses = {ru["status"] for ru in runs}
    fwd_models = {ru["forwarded_model"] for ru in runs}
    results.append({
        "cell": cell_id, "description": desc, "route": route,
        "allowlist": ["cheap-model"],
        "n": N,
        "status": sorted(statuses)[0] if len(statuses) == 1 else list(statuses),
        "status_consistent": len(statuses) == 1,
        "forwarded_model": sorted(x for x in fwd_models if x)[0] if len(fwd_models) == 1 and None not in fwd_models else list(fwd_models),
        "runs": runs,
    })
    served = results[-1]["forwarded_model"]
    print(f"{cell_id:42s} status={results[-1]['status']} forwarded={served} ({N}/{N})")

with open(os.path.join(OUT, "results.json"), "w") as f:
    json.dump({"build": LABEL, "n": N, "cells": results}, f, indent=2)
print(f"\nwrote {os.path.join(OUT, 'results.json')}")
