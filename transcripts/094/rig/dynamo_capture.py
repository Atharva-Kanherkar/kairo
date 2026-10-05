#!/usr/bin/env python3
"""Drive the Dynamo frontend (through the recording proxy on :18001) for finding 094."""
import json, sys, urllib.request
BASE = "http://localhost:18001"
M = "Qwen/Qwen3-0.6B"
def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": "dynamo-local"})
    return urllib.request.urlopen(req, timeout=60).read().decode()
def anth(stop, stream):
    b = {"model": M, "max_tokens": 200, "messages": [{"role": "user", "content": "SCRIPT:answer"}]}
    if stop: b["stop_sequences"] = ["</answer>", "\n\nHuman:"]
    if stream: b["stream"] = True
    raw = post("/v1/messages", b)
    if not stream:
        d = json.loads(raw); return d["stop_reason"], d["stop_sequence"], d["content"][0]["text"]
    sr = ss = None; text = ""
    for line in raw.splitlines():
        if line.startswith("data:") and line[5:].strip() != "[DONE]":
            e = json.loads(line[5:])
            if e["type"] == "message_delta": sr, ss = e["delta"].get("stop_reason"), e["delta"].get("stop_sequence")
            if e["type"] == "content_block_delta": text += e["delta"].get("text", "")
    return sr, ss, text
def chat():
    b = {"model": M, "max_tokens": 200, "stop": ["</answer>", "\n\nHuman:"],
         "messages": [{"role": "user", "content": "SCRIPT:answer"}], "nvext": {"extra_fields": ["stop_reason"]}}
    d = json.loads(post("/v1/chat/completions", b))
    return d["choices"][0]["finish_reason"], d.get("nvext", {}).get("stop_reason"), d["choices"][0]["message"]["content"]
for label, fn, n in [("bug messages non-stream", lambda: anth(True, False), 5),
                     ("bug messages stream", lambda: anth(True, True), 3),
                     ("control same-frontend chat (nvext.stop_reason)", chat, 3),
                     ("control trigger removed (no stop_sequences)", lambda: anth(False, False), 3)]:
    for i in range(n):
        r = fn(); print(f"{label} #{i+1}: reason={r[0]!r} matched={r[1]!r} text_tail={r[2][-32:]!r}")
