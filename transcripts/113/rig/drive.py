# Driver for finding 113. Runs the control ladder N times per cell against a
# running LiteLLM proxy, saving the client request/response bytes and the exact
# forwarded upstream bytes (matched from the fake upstream's scratch dir) for
# each run, then writes a results matrix.
#
# Usage: drive.py <proxy_base> <key> <scratch_dir> <out_dir> <n> [label]
import sys, os, json, glob, shutil, time, re
import urllib.request

PROXY, KEY, SCRATCH, OUT, N = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
LABEL = sys.argv[6] if len(sys.argv) > 6 else "main"
os.makedirs(OUT, exist_ok=True)

GC_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}

# cell: (cell_id, method, route, auth_type, body_dict, description)
CELLS = [
    ("gemini-v1-path-denied", "POST", "/gemini/v1/models/premium-model:generateContent", "query",
     GC_BODY, "gemini passthrough v1 GA route with denied premium-model (TRIGGER)"),
    ("gemini-v1-stream-path-denied", "POST", "/gemini/v1/models/premium-model:streamGenerateContent", "query",
     GC_BODY, "gemini passthrough v1 stream route with denied premium-model (TRIGGER)"),
    ("gemini-v1alpha-path-denied", "POST", "/gemini/v1alpha/models/premium-model:generateContent", "query",
     GC_BODY, "gemini passthrough v1alpha route with denied premium-model (TRIGGER)"),
    ("gemini-models-path-denied", "POST", "/gemini/models/premium-model:generateContent", "query",
     GC_BODY, "gemini passthrough /models/ prefix with denied premium-model (TRIGGER)"),
    ("gemini-v1beta-control", "POST", "/gemini/v1beta/models/premium-model:generateContent", "query",
     GC_BODY, "gemini passthrough v1beta route with denied premium-model (CONTROL, 403)"),
    ("gemini-v1-cheap-control", "POST", "/gemini/v1/models/cheap-model:generateContent", "query",
     GC_BODY, "gemini passthrough v1 route with allowed cheap-model (CONTROL, 200)"),
]


def scratch_indices():
    return {int(os.path.basename(p)[:4]) for p in glob.glob(os.path.join(SCRATCH, "*-request.http"))}


def forwarded_model(idx):
    try:
        with open(os.path.join(SCRATCH, f"{idx:04d}-request.http"), "rb") as f:
            raw = f.read()
        req_line = raw.split(b"\n", 1)[0].decode()
        m = re.search(r"/models/([^:]+):", req_line)
        if m:
            return m.group(1)
        return None
    except Exception:
        return None


def call(route, method, headers, body):
    url = PROXY + route
    sep = "&" if "?" in route else "?"
    url = f"{url}{sep}key={KEY}"
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
    headers = {}
    runs = []
    statuses = []
    forwarded_models = []
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
        
        redacted_route = f"{route}?key=[REDACTED]" if "?" not in route else f"{route}&key=[REDACTED]"
        with open(os.path.join(run_dir, "client-request.http"), "wb") as f:
            f.write(f"{method} {redacted_route} HTTP/1.1\n".encode())
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
        statuses.append(status)
        forwarded_models.append(fwd_model)

    status_consistent = len(set(statuses)) == 1
    model_consistent = len(set(forwarded_models)) == 1
    results.append({
        "cell": cell_id,
        "description": desc,
        "route": route,
        "runs": runs,
        "expected_status": 403 if "denied" in cell_id and "control" in cell_id else 200,
        "status_consistent": status_consistent,
        "model_consistent": model_consistent,
        "final_status": statuses[0],
        "final_forwarded_model": forwarded_models[0],
    })

matrix_file = os.path.join(OUT, "results.json")
with open(matrix_file, "w") as f:
    json.dump({"label": LABEL, "cells": results}, f, indent=2)

print(f"Results written to {matrix_file}")
