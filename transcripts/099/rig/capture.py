"""Send each case to the running Dynamo frontend over a raw socket and save the
exact request bytes, the exact response bytes, and the scripted engine's record
of what it emitted for that request.

usage: capture.py <outdir> <runs> <case>...
"""
import glob, json, os, shutil, socket, sys, time

OTOOLS = [{"type": "function", "function": {"name": "write_file", "description": "Write a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]
ATOOLS = [{"name": "write_file", "description": "Write a file", "input_schema": OTOOLS[0]["function"]["parameters"]}]
ENGINE_DIR = os.environ.get("CAPTURE_DIR", "/rig/captures")


def is_final(rec):
    """The engine first saves its record with termination "running" and empty
    emitted bytes, then rewrites it when generation ends."""
    return bool(rec) and rec.get("termination") not in (None, "", "running")


def wait_final(engine_dir, before, attempts=50, interval=0.1):
    """Return the newest engine record created after `before`, once it is final.
    Returns the last record seen, possibly still running, if time runs out."""
    rec = None
    for _ in range(attempts):
        new = sorted(set(glob.glob(f"{engine_dir}/forwarded-*.json")) - before)
        if new:
            try:
                rec = json.load(open(new[-1]))
            except json.JSONDecodeError:
                rec = None  # caught mid-write
            if is_final(rec):
                return rec
        time.sleep(interval)
    return rec


def raw_request(path, body, extra):
    b = json.dumps(body, separators=(",", ":")).encode()
    head = f"POST {path} HTTP/1.1\r\nHost: localhost:8000\r\nContent-Type: application/json\r\n{extra}Content-Length: {len(b)}\r\nConnection: close\r\n\r\n"
    return head.encode() + b


def send(req):
    s = socket.create_connection(("127.0.0.1", 8000), timeout=60)
    s.sendall(req)
    out = b""
    while chunk := s.recv(65536):
        out += chunk
    s.close()
    return out


def main():
    out, runs, cases = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
    for case in cases:
        for dialect in ("openai", "anthropic"):
            for stream in (False, True):
                for i in range(1, runs + 1):
                    cell = os.path.join(out, f"{case}-{dialect}-{'stream' if stream else 'unary'}-{i:02d}")
                    os.makedirs(cell, exist_ok=True)
                    msgs = [{"role": "user", "content": f"SCRIPT:{case} write the fixture"}]
                    if dialect == "openai":
                        req = raw_request("/v1/chat/completions", {"model": "Qwen/Qwen3-0.6B", "stream": stream, "max_tokens": 400, "tools": OTOOLS, "messages": msgs}, "")
                    else:
                        req = raw_request("/v1/messages", {"model": "Qwen/Qwen3-0.6B", "stream": stream, "max_tokens": 400, "tools": ATOOLS, "messages": msgs}, "anthropic-version: 2023-06-01\r\n")
                    before = set(glob.glob(f"{ENGINE_DIR}/forwarded-*.json"))
                    resp = send(req)
                    open(os.path.join(cell, "client-request.http"), "wb").write(req)
                    open(os.path.join(cell, "client-response.http"), "wb").write(resp)
                    rec = wait_final(ENGINE_DIR, before)
                    if not is_final(rec):
                        sys.exit(f"{cell}: no final engine record")
                    keep = {k: rec[k] for k in ("script", "planned_completion", "termination", "emitted_token_ids", "emitted_text")}
                    json.dump(keep, open(os.path.join(cell, "engine-emitted.json"), "w"), indent=1, ensure_ascii=False)
                    print(cell, resp.split(b"\r\n", 1)[0].decode(), rec["termination"])


if __name__ == "__main__":
    main()
