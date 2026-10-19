"""5 trials x 4 cases. Each trial sends the captured opencode turn twice (stream, like opencode)
with a fresh nonce in the system text, and records which relay exchanges each call produced."""
import glob, httpx, json, os, time, uuid
ROOT = os.environ["ROOT"]
CLIENT = "http://127.0.0.1:19971/openai/v1/chat/completions"
DIRECT = "http://127.0.0.1:19974/api/v1/chat/completions"
msg = json.load(open("req-oc-turn.json")); blk = json.load(open("req-block-level.json"))
CASES = [
    ("anthropic-message-level", CLIENT, "anthropic/claude-haiku-4-5", msg, False),
    ("anthropic-block-level-control", CLIENT, "anthropic/claude-haiku-4-5", blk, False),
    ("openrouter-message-level", CLIENT, "openrouter/anthropic/claude-haiku-4.5", msg, False),
    ("openrouter-block-level-control", CLIENT, "openrouter/anthropic/claude-haiku-4.5", blk, False),
    ("direct-openrouter-message-level-control", DIRECT, "anthropic/claude-haiku-4.5", msg, True),
]
def files():
    return {d: sorted(glob.glob(f"{ROOT}/{d}/*-request.http")) for d in ("client-to-bifrost","bifrost-to-anthropic","bifrost-to-openrouter","client-to-openrouter-direct")}
def call(url, body, direct):
    hdr = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"} if direct else {}
    u = None; st = None
    with httpx.stream("POST", url, json=body, headers=hdr, timeout=120) as r:
        st = r.status_code
        for line in r.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                try: d = json.loads(line[6:])
                except Exception: continue
                u = d.get("usage") or u
            elif st != 200: pass
        if st != 200: u = {"error_body_seen": True}
    return st, u
rows = []
for trial in range(1, 6):
    for name, url, model, base, direct in CASES:
        nonce = uuid.uuid4().hex
        for callno in (1, 2):
            b = json.loads(json.dumps(base)); b.update(model=model, max_tokens=16, stream=True, stream_options={"include_usage": True})
            sysm = b["messages"][0]
            if isinstance(sysm["content"], str): sysm["content"] = f"[trial {nonce}]\n" + sysm["content"]
            else: sysm["content"][0]["text"] = f"[trial {nonce}]\n" + sysm["content"][0]["text"]
            before = files(); st, u = call(url, b, direct); time.sleep(1.0); after = files()
            new = {d: [os.path.basename(f) for f in after[d] if f not in before[d]] for d in after}
            pd = (u or {}).get("prompt_tokens_details") or {}
            row = dict(trial=trial, case=name, call=callno, model=model, status=st, prompt_tokens=(u or {}).get("prompt_tokens"),
                       cache_write_tokens=pd.get("cache_write_tokens"), cached_tokens=pd.get("cached_tokens"), cost=(u or {}).get("cost"),
                       exchanges={d: v for d, v in new.items() if v})
            rows.append(row); print(json.dumps(row), flush=True)
            time.sleep(0.5)
json.dump(rows, open(f"{ROOT}/results.json", "w"), indent=1)
