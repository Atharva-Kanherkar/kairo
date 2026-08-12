import json, sys, glob

def check(path):
    blocks = {}   # index -> {type, name, parts:[], started, stopped}
    order_errs = []
    stop_reason = None
    for line in open(path):
        line = line.strip()
        if not line.startswith("data: "): continue
        ev = json.loads(line[6:])
        t = ev.get("type")
        if t == "content_block_start":
            i = ev["index"]
            if i in blocks: order_errs.append(f"block {i} started twice")
            cb = ev["content_block"]
            blocks[i] = {"type": cb["type"], "name": cb.get("name"), "parts": [],
                         "start_input": cb.get("input"), "stopped": False}
        elif t == "content_block_delta":
            i = ev["index"]
            if i not in blocks: order_errs.append(f"delta for unstarted block {i}")
            elif blocks[i]["stopped"]: order_errs.append(f"delta after stop, block {i}")
            else:
                d = ev["delta"]
                if d["type"] == "input_json_delta": blocks[i]["parts"].append(d["partial_json"])
                elif d["type"] == "text_delta": blocks[i]["parts"].append(d["text"])
        elif t == "content_block_stop":
            i = ev["index"]
            if i not in blocks: order_errs.append(f"stop for unstarted block {i}")
            else: blocks[i]["stopped"] = True
        elif t == "message_delta":
            stop_reason = ev["delta"].get("stop_reason")
    print(f"\n== {path}")
    print(f"   stop_reason={stop_reason}  order_errors={order_errs or 'none'}")
    for i, b in sorted(blocks.items()):
        joined = "".join(b["parts"])
        if b["type"] == "tool_use":
            try:
                parsed = json.loads(joined) if joined else {}
                keys = list(parsed.keys()); clen = len(str(parsed.get("content","")))
                print(f"   block {i}: tool_use name={b['name']} args_valid_json=True keys={keys} content_len={clen}")
            except json.JSONDecodeError as e:
                print(f"   block {i}: tool_use name={b['name']} args_valid_json=FALSE ({e}) joined_len={len(joined)} tail={joined[-60:]!r}")
        else:
            print(f"   block {i}: {b['type']} len={len(joined)}")

for p in sorted(glob.glob(sys.argv[1])): check(p)
