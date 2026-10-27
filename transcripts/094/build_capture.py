#!/usr/bin/env python3
"""Build the machine-readable captures for finding 094 from the raw exchanges.

Each record describes one Anthropic Messages exchange:
  label, source (raw response file), stream, stop_sequences (requested),
  generation (the text the engine actually handed to the Dynamo runtime,
              `emitted_text` in the forwarded record, never the planned
              completion; null when the upstream is a hosted provider),
  text (concatenated text the client received), stop_reason, stop_sequence,
  max_tokens (requested), output_tokens (usage reported to the client).

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
        return out, d.get("stop_reason"), d.get("stop_sequence"), d.get("usage", {}).get("output_tokens")
    out, sr, ss, used = "", None, None, None
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
            used = e.get("usage", {}).get("output_tokens", used)
    return out, sr, ss, used


def dynamo_records(subdir, label_prefix):
    reqs = sorted(glob.glob(os.path.join(HERE, subdir, "*-request.http")))
    fwd = sorted(glob.glob(os.path.join(HERE, subdir, "forwarded", "forwarded-*.json")))
    gens = [json.load(open(f)) for f in fwd]
    # One engine record per exchange, in order. Check the pairing instead of
    # trusting it.
    assert len(gens) == len(reqs), (subdir, len(gens), len(reqs))
    recs = []
    for rq, gen in zip(reqs, gens):
        path = open(rq, "rb").read().split(b" ", 2)[1].decode()
        b = json.loads(body(rq))
        cond = gen["request"]["stop_conditions"]
        assert cond["max_tokens"] == b["max_tokens"], rq
        assert (cond.get("stop") or []) == (b.get("stop_sequences") or b.get("stop") or []), rq
        assert gen["termination"] != "running", rq
        if path != "/v1/messages":
            continue
        rs = rq.replace("-request.http", "-response.http")
        stream = bool(b.get("stream"))
        text, sr, ss, used = parse_messages_response(body(rs), stream)
        kind = "trigger-removed" if not b.get("stop_sequences") else (
            "length-boundary" if b["max_tokens"] < 200 else "bug")
        recs.append({
            "label": f"{label_prefix} {'stream' if stream else 'non-stream'} {kind}",
            "kind": kind,
            "source": os.path.relpath(rs, HERE),
            "engine_record": os.path.relpath(fwd[gens.index(gen)], HERE),
            "termination": gen["termination"],
            "stream": stream,
            "stop_sequences": b.get("stop_sequences") or [],
            "generation": gen["emitted_text"],
            "text": text,
            "stop_reason": sr,
            "stop_sequence": ss,
            "max_tokens": b["max_tokens"],
            "output_tokens": used,
        })
    return recs


def live_records():
    recs = []
    for rs in sorted(glob.glob(os.path.join(HERE, "anthropic-live", "*-response.http"))):
        rq = rs.replace("-response.http", "-request.http")
        b = json.loads(body(rq))
        stream = bool(b.get("stream"))
        text, sr, ss, used = parse_messages_response(body(rs), stream)
        recs.append({
            "label": f"live Anthropic API {'stream' if stream else 'non-stream'}",
            "source": os.path.relpath(rs, HERE),
            "stream": stream,
            "stop_sequences": b.get("stop_sequences") or [],
            "generation": None,  # hosted provider: raw generation is not observable
            "text": text,
            "stop_reason": sr,
            "stop_sequence": ss,
            "max_tokens": b.get("max_tokens"),
            "output_tokens": used,
        })
    return recs


def write(name, recs):
    with open(os.path.join(HERE, name), "w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(name, len(recs))


dyn = dynamo_records("dynamo", "Dynamo 1.6.0.dev20261004")
rel = dynamo_records("dynamo-release-1.5.0", "Dynamo 1.5.0")
write("capture-dynamo-bug.jsonl", [r for r in dyn if r["kind"] == "bug"])
write("capture-dynamo-release-1.5.0-bug.jsonl", [r for r in rel if r["kind"] == "bug"])
write("capture-dynamo-trigger-removed.jsonl",
      [r for r in dyn + rel if r["kind"] == "trigger-removed"])
write("capture-dynamo-length-boundary.jsonl",
      [r for r in dyn + rel if r["kind"] == "length-boundary"])
write("capture-provider-control.jsonl", live_records())
