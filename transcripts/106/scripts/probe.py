#!/usr/bin/env python3
"""Mid-stream worker-kill probe for Dynamo request migration.

Sends one streaming request, waits for K content-bearing SSE events, finds the
worker whose generate endpoint has the request in flight, kills it, then reads
the stream to the end. Saves the raw request and response bytes and a parsed
summary. Run with --kill none for the control.
"""
import argparse, json, os, re, signal, subprocess, sys, time, urllib.request
import httpx

MODEL = os.environ.get("MODEL", "Qwen/Qwen3-4B-Instruct-2507")
BASE = "http://127.0.0.1:8000"

COUNT_PROMPT = ("Write the integers from 1 to 250 in increasing order, separated by single "
                "spaces. Output nothing else.")

TOOLS = [{"type": "function", "function": {
    "name": "write_file",
    "description": "Write text content to a file.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"]}}}]
TOOL_PROMPT = ("Call write_file exactly once with path 'nums.txt' and content equal to the "
               "integers from 1 to 200 in increasing order separated by single spaces.")


def body_for(cell):
    if cell == "chat":
        return "/v1/chat/completions", {"model": MODEL, "stream": True, "temperature": 0,
            "max_tokens": 1500, "messages": [{"role": "user", "content": COUNT_PROMPT}],
            "stream_options": {"include_usage": True}}
    if cell == "chat_logprobs":
        p, b = body_for("chat"); b["logprobs"] = True; b["top_logprobs"] = 2; return p, b
    if cell == "chat_stop":
        p, b = body_for("chat"); b["stop"] = [" 180 "]; return p, b
    if cell == "chat_tools":
        return "/v1/chat/completions", {"model": MODEL, "stream": True, "temperature": 0,
            "max_tokens": 1500, "tools": TOOLS, "tool_choice": "auto",
            "messages": [{"role": "user", "content": TOOL_PROMPT}],
            "stream_options": {"include_usage": True}}
    if cell == "messages":
        return "/v1/messages", {"model": MODEL, "stream": True, "temperature": 0,
            "max_tokens": 1500, "messages": [{"role": "user", "content": COUNT_PROMPT}]}
    if cell == "messages_tools":
        return "/v1/messages", {"model": MODEL, "stream": True, "temperature": 0,
            "max_tokens": 1500, "messages": [{"role": "user", "content": TOOL_PROMPT}],
            "tools": [{"name": "write_file", "description": "Write text content to a file.",
                       "input_schema": TOOLS[0]["function"]["parameters"]}]}
    if cell == "responses":
        return "/v1/responses", {"model": MODEL, "stream": True, "temperature": 0,
            "max_output_tokens": 1500, "input": COUNT_PROMPT}
    if cell == "responses_tools":
        return "/v1/responses", {"model": MODEL, "stream": True, "temperature": 0,
            "max_output_tokens": 1500, "input": TOOL_PROMPT,
            "tools": [{"type": "function", "name": "write_file", "description": "Write text content to a file.",
                       "parameters": TOOLS[0]["function"]["parameters"]}]}
    if cell == "nonstream":
        p, b = body_for("chat"); b["stream"] = False; b.pop("stream_options"); return p, b
    raise SystemExit(f"unknown cell {cell}")


def inflight(w):
    try:
        txt = urllib.request.urlopen(f"http://127.0.0.1:{9100+w}/metrics", timeout=2).read().decode()
    except Exception:
        return None
    m = re.search(r'dynamo_component_inflight_requests\{[^}]*dynamo_endpoint="generate"[^}]*\} (\S+)', txt)
    return float(m.group(1)) if m else None


def kill_serving(sig):
    for _ in range(50):
        for w in (1, 2):
            if (inflight(w) or 0) >= 1:
                pid = int(open(f"/work/worker{w}.pid").read())
                # kill the worker and its engine-core children
                kids = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
                os.kill(pid, sig)
                if sig == signal.SIGKILL:
                    for k in kids:
                        try: os.kill(int(k), sig)
                        except ProcessLookupError: pass
                return w, pid
        time.sleep(0.02)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cell"); ap.add_argument("--kill", default="none", choices=["none", "sigkill", "sigterm"])
    ap.add_argument("--after", type=int, default=60); ap.add_argument("--out", required=True)
    ap.add_argument("--after-secs", type=float, default=None)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    path, body = body_for(a.cell)
    raw_req = json.dumps(body).encode()
    open(f"{a.out}/client-request.json", "wb").write(raw_req)
    events, killed, t0 = 0, None, time.time()
    raw = bytearray()
    if a.after_secs is not None and a.kill != "none":
        import threading
        def timed():
            nonlocal killed
            time.sleep(a.after_secs)
            killed = kill_serving(signal.SIGKILL if a.kill == "sigkill" else signal.SIGTERM)
            open(f"{a.out}/kill.json", "w").write(json.dumps(
                {"worker": killed[0], "pid": killed[1], "after_secs": a.after_secs, "signal": a.kill}))
        threading.Thread(target=timed, daemon=True).start()
        a.after = 10**9
    with httpx.Client(timeout=300) as c:
        if not body["stream"]:
            # non-stream: kill after a fixed delay instead of K events
            import threading
            r = c.post(BASE + path, content=raw_req, headers={"content-type": "application/json"})
            raw += f"HTTP {r.status_code}\n".encode() + r.content
        else:
            with c.stream("POST", BASE + path, content=raw_req, headers={"content-type": "application/json"}) as r:
                raw += f"HTTP {r.status_code}\n".encode()
                for chunk in r.iter_raw():
                    raw += chunk
                    if a.kill != "none" and killed is None and a.after < 10**9:
                        events = raw.count(b"\ndata:") + raw.count(b"\n\ndata:")
                        if events >= a.after:
                            killed = kill_serving(signal.SIGKILL if a.kill == "sigkill" else signal.SIGTERM)
                            open(f"{a.out}/kill.json", "w").write(json.dumps(
                                {"worker": killed[0], "pid": killed[1], "after_events": events,
                                 "t": time.time() - t0, "signal": a.kill}))
    open(f"{a.out}/client-response.raw", "wb").write(bytes(raw))
    print(json.dumps({"cell": a.cell, "kill": a.kill, "killed": killed, "bytes": len(raw),
                      "secs": round(time.time() - t0, 2)}))


if __name__ == "__main__":
    main()
