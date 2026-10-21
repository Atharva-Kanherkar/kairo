#!/usr/bin/env python3
"""Parse a saved client-response.raw from probe.py and check stream invariants."""
import json, re, sys
from collections import Counter


def sse_events(raw):
    text = raw.split(b"\n", 1)[1].decode("utf-8", "replace") if raw.startswith(b"HTTP") else raw.decode()
    out = []
    for block in re.split(r"\r?\n\r?\n", text):
        ev, data = None, []
        for line in block.splitlines():
            if line.startswith("event:"): ev = line[6:].strip()
            elif line.startswith("data:"): data.append(line[5:].lstrip())
        if data: out.append((ev, "\n".join(data)))
    return out


def seq_check(text):
    toks = text.split()
    nums = [int(t) for t in toks if t.isdigit()]
    nondig = [t for t in toks if not t.isdigit()]
    dup = [n for n, c in Counter(nums).items() if c > 1]
    gaps = [(a, b) for a, b in zip(nums, nums[1:]) if b != a + 1]
    return {"n": len(nums), "first": nums[:1], "last": nums[-1:], "dups": dup[:10],
            "gaps": gaps[:10], "nondigit": nondig[:5]}


def chat(raw):
    if b'"object":"chat.completion"' in raw[:400] or b"chat.completion\"" in raw and b"data:" not in raw:
        body = json.loads(raw.split(b"\n", 1)[1])
        m = body["choices"][0]["message"]
        return {"content": m.get("content") or "", "tool_calls": m.get("tool_calls"),
                "finish": body["choices"][0]["finish_reason"], "usage": body.get("usage"),
                "errors": [], "events": 1}
    content, calls, finish, usage, errors, lp, ids = "", {}, [], None, [], 0, set()
    evs = sse_events(raw)
    for ev, d in evs:
        if d == "[DONE]": continue
        try: j = json.loads(d)
        except Exception: errors.append(d[:200]); continue
        if ev == "error" or "error" in j: errors.append(d[:300]); continue
        ids.add(j.get("id"))
        if j.get("usage"): usage = j["usage"]
        for ch in j.get("choices", []):
            dl = ch.get("delta", {})
            content += dl.get("content") or ""
            for tc in dl.get("tool_calls") or []:
                c = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "args": "", "id_count": 0})
                if tc.get("id"): c["id"] = tc["id"]; c["id_count"] += 1
                f = tc.get("function") or {}
                c["name"] += f.get("name") or ""; c["args"] += f.get("arguments") or ""
            if ch.get("logprobs") and ch["logprobs"].get("content"): lp += len(ch["logprobs"]["content"])
            if ch.get("finish_reason"): finish.append(ch["finish_reason"])
    return {"content": content, "tool_calls": calls, "finish": finish, "usage": usage,
            "errors": errors, "logprob_entries": lp, "ids": len(ids), "events": len(evs)}


def messages(raw):
    evs = sse_events(raw)
    names = [e for e, _ in evs]
    text, blocks, stop, usage, errors, open_blocks, issues = "", {}, [], {}, [], set(), []
    for ev, d in evs:
        if d == "[DONE]": continue
        j = json.loads(d)
        t = j.get("type", ev)
        if t == "error": errors.append(d[:300])
        elif t == "content_block_start":
            i = j["index"]
            if i in blocks: issues.append(f"block {i} started twice")
            blocks[i] = {"type": j["content_block"]["type"], "text": "", "json": "",
                         "name": j["content_block"].get("name")}
            open_blocks.add(i)
        elif t == "content_block_delta":
            i = j["index"]
            if i not in open_blocks: issues.append(f"delta for closed/unknown block {i}")
            dl = j["delta"]
            if dl["type"] == "text_delta": blocks.setdefault(i, {"text": "", "json": ""})["text"] += dl["text"]; text += dl["text"]
            elif dl["type"] == "input_json_delta": blocks.setdefault(i, {"text": "", "json": ""})["json"] += dl["partial_json"]
        elif t == "content_block_stop":
            if j["index"] not in open_blocks: issues.append(f"stop for unopened block {j['index']}")
            open_blocks.discard(j["index"])
        elif t == "message_delta":
            stop.append(j["delta"].get("stop_reason")); usage.update(j.get("usage") or {})
        elif t == "message_start":
            usage.update(j["message"].get("usage") or {})
    c = Counter(names)
    if c["message_start"] != 1: issues.append(f"message_start x{c['message_start']}")
    if c["message_stop"] != 1: issues.append(f"message_stop x{c['message_stop']}")
    if open_blocks: issues.append(f"unclosed blocks {sorted(open_blocks)}")
    return {"content": text, "blocks": blocks, "stop": stop, "usage": usage, "errors": errors,
            "issues": issues, "event_counts": dict(c)}


def responses(raw):
    evs = sse_events(raw)
    names = [e for e, _ in evs]
    text, usage, errors, status, fargs = "", None, [], [], []
    for ev, d in evs:
        if d == "[DONE]": continue
        j = json.loads(d)
        t = j.get("type", ev)
        if t == "response.output_text.delta": text += j["delta"]
        elif t == "response.function_call_arguments.done": fargs.append(j.get("arguments", ""))
        elif t in ("response.completed", "response.incomplete", "response.failed"):
            usage = j["response"].get("usage"); status.append(j["response"].get("status"))
        elif t == "error" or "error" in t: errors.append(d[:300])
    c = Counter(names)
    issues = []
    if c["response.created"] != 1: issues.append(f"created x{c['response.created']}")
    if len(status) != 1: issues.append(f"terminal events {status}")
    return {"content": text, "usage": usage, "status": status, "errors": errors, "issues": issues, "fargs": fargs,
            "event_counts": dict(c)}


def analyze(path, cell):
    raw = open(path, "rb").read()
    status = raw.split(b"\n", 1)[0].decode()
    if cell.startswith("messages"): r = messages(raw)
    elif cell.startswith("responses"): r = responses(raw)
    else: r = chat(raw)
    r["http"] = status
    r["seq"] = seq_check(r["content"]) if r["content"] else None
    if cell.endswith("tools"):
        if cell == "responses_tools":
            r["tool_args"] = r["fargs"]
        elif cell.startswith("messages"):
            tb = [b for b in r["blocks"].values() if b.get("type") == "tool_use"]
            r["tool_args"] = [b["json"] for b in tb]
        else:
            r["tool_args"] = [c["args"] for c in r["tool_calls"].values()] if r["tool_calls"] else []
        parsed = []
        for s in r["tool_args"]:
            try: parsed.append(seq_check(json.loads(s)["content"]))
            except Exception as e: parsed.append({"invalid_json": str(e)[:80], "tail": s[-80:]})
        r["tool_seq"] = parsed
    return r


if __name__ == "__main__":
    r = analyze(sys.argv[1], sys.argv[2])
    r.pop("blocks", None)
    r["content"] = (r["content"][:60] + " ... " + r["content"][-60:]) if len(r["content"]) > 130 else r["content"]
    print(json.dumps(r, default=str))
