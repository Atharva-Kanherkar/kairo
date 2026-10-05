#!/usr/bin/env python3
"""Build the machine-readable captures for finding 094 from the raw exchanges.

Each record describes one Anthropic Messages exchange:
  label, source (raw response file), stream, stop_sequences (requested),
  generation (what the engine actually generated, from the forwarded record;
              null when the upstream is a hosted provider and not observable),
  text (concatenated text the client received), stop_reason, stop_sequence.

usage: python3 build_capture.py   (run from transcripts/094)
"""
import glob, json, os

HERE = os.path.dirname(os.path.abspath(__file__))


def body(path):
    raw = open(path, "rb").read()
    return raw.split(b"\r\n\r\n", 1)[1].decode("utf-8")


def parse_messages_response(text, stream):
    if not stream:
        d = json.loads(text)
        out = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        return out, d.get("stop_reason"), d.get("stop_sequence")
    out, sr, ss = "", None, None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            continue
        e = json.loads(payload)
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "text_delta":
            out += e["delta"]["text"]
        if e.get("type") == "message_delta":
            sr = e["delta"].get("stop_reason")
            ss = e["delta"].get("stop_sequence")
    return out, sr, ss


def dynamo_records(subdir, label_prefix):
    reqs = sorted(glob.glob(os.path.join(HERE, subdir, "*-request.http")))
    fwd = sorted(glob.glob(os.path.join(HERE, subdir, "forwarded", "*.json")))
    gens = [json.load(open(f)) for f in fwd]
    recs, gi = [], 0
    for rq in reqs:
        path = open(rq, "rb").read().split(b" ", 2)[1].decode()
        b = json.loads(body(rq))
        rs = rq.replace("-request.http", "-response.http")
        if gi < len(gens):
            gen = gens[gi]
        else:
            gen = None
        gi += 1
        if path != "/v1/messages":
            continue
        stream = bool(b.get("stream"))
        text, sr, ss = parse_messages_response(body(rs), stream)
        recs.append({
            "label": f"{label_prefix} {'stream' if stream else 'non-stream'}"
                     f"{'' if b.get('stop_sequences') else ' trigger-removed'}",
            "source": os.path.relpath(rs, HERE),
            "stream": stream,
            "stop_sequences": b.get("stop_sequences") or [],
            "generation": gen["scripted_completion"] if gen else None,
            "text": text,
            "stop_reason": sr,
            "stop_sequence": ss,
        })
    return recs


def live_records():
    recs = []
    for rs in sorted(glob.glob(os.path.join(HERE, "anthropic-live", "*-response.http"))):
        rq = rs.replace("-response.http", "-request.http")
        b = json.loads(body(rq))
        stream = bool(b.get("stream"))
        text, sr, ss = parse_messages_response(body(rs), stream)
        recs.append({
            "label": f"live Anthropic API {'stream' if stream else 'non-stream'}",
            "source": os.path.relpath(rs, HERE),
            "stream": stream,
            "stop_sequences": b.get("stop_sequences") or [],
            "generation": None,  # hosted provider: raw generation is not observable
            "text": text,
            "stop_reason": sr,
            "stop_sequence": ss,
        })
    return recs


def write(name, recs):
    with open(os.path.join(HERE, name), "w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(name, len(recs))


dyn = dynamo_records("dynamo", "Dynamo 1.6.0.dev20261004")
write("capture-dynamo-bug.jsonl", [r for r in dyn if r["stop_sequences"]])
write("capture-dynamo-trigger-removed.jsonl", [r for r in dyn if not r["stop_sequences"]])
write("capture-provider-control.jsonl", live_records())
