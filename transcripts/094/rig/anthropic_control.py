#!/usr/bin/env python3
"""Provider control: same stop_sequences request to the real Anthropic Messages API.

Saves sanitized raw request/response bytes per run. Reads ANTHROPIC_API_KEY from the
environment (never printed). Tenant headers are redacted in the saved response.
usage: anthropic_control.py <outdir> <runs> <stream:0|1>
"""
import json, os, sys, urllib.request

out, runs, stream = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1"
os.makedirs(out, exist_ok=True)
MODEL = os.environ.get("CONTROL_MODEL", "claude-haiku-4-5")
STOP = os.environ.get("STOP_SEQ", "</answer>")
PROMPT = (
    "Reply with exactly the following text and nothing else, character for character:\n"
    f"<answer>2 plus 3 is 5</answer>\nThis text must never be generated."
) if STOP == "</answer>" else os.environ["PROMPT_TEXT"]
REDACT = {"x-api-key", "authorization", "anthropic-organization-id", "cf-ray", "set-cookie"}
body = {"model": MODEL, "max_tokens": 200, "temperature": 0,
        "stop_sequences": [STOP, "\n\nHuman:"],
        "messages": [{"role": "user", "content": PROMPT}]}
if stream:
    body["stream"] = True
data = json.dumps(body).encode()
hdrs = {"content-type": "application/json", "anthropic-version": "2023-06-01",
        "x-api-key": os.environ["ANTHROPIC_API_KEY"]}
for i in range(1, runs + 1):
    tag = f"run-{i:02d}{'-stream' if stream else ''}"
    with open(f"{out}/{tag}-request.http", "wb") as f:
        f.write(b"POST /v1/messages HTTP/1.1\r\nHost: api.anthropic.com\r\n")
        for k, v in hdrs.items():
            f.write(f"{k}: {'[REDACTED]' if k.lower() in REDACT else v}\r\n".encode())
        f.write(b"\r\n" + data)
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=data, headers=hdrs)
    try:
        r = urllib.request.urlopen(req, timeout=120); status, rh, rb = r.status, r.getheaders(), r.read()
    except urllib.error.HTTPError as e:
        status, rh, rb = e.code, e.headers.items(), e.read()
    with open(f"{out}/{tag}-response.http", "wb") as f:
        f.write(f"HTTP/1.1 {status}\r\n".encode())
        for k, v in rh:
            kl = k.lower()
            red = kl in ("anthropic-workspace-id", "traceresponse") or kl in REDACT or kl.startswith("anthropic-ratelimit") or kl == "request-id" or kl == "x-request-id"
            f.write(f"{k}: {'[REDACTED]' if red else v}\r\n".encode())
        f.write(b"\r\n" + rb)
    text = rb.decode()
    if stream:
        sr = ss = None
        for line in text.splitlines():
            if line.startswith("data:") and '"message_delta"' in line:
                d = json.loads(line[5:])["delta"]; sr, ss = d.get("stop_reason"), d.get("stop_sequence")
        print(tag, status, "stop_reason", sr, "stop_sequence", repr(ss))
    else:
        d = json.loads(text)
        print(tag, status, "stop_reason", d.get("stop_reason"), "stop_sequence", repr(d.get("stop_sequence")),
              "text_tail", repr(d["content"][0]["text"][-30:]) if d.get("content") else None)
