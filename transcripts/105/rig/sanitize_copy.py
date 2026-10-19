"""Copy captures into kairo with tenant identifiers redacted. Request auth headers were
already written as <redacted> by the relay; this also redacts response tenant headers
and Anthropic request ids in bodies. The bytes under dispute (bodies) are untouched otherwise."""
import os, re, shutil, sys
SRC, DST = sys.argv[1], sys.argv[2]
HDR = {"request-id", "traceresponse", "anthropic-organization-id", "anthropic-workspace-id", "cf-ray", "set-cookie",
       "x-request-id", "openai-organization", "openai-project", "x-generation-id"}
for root, _, files in os.walk(SRC):
    for name in files:
        if not name.endswith((".http", ".json")): continue
        src = os.path.join(root, name); rel = os.path.relpath(src, SRC); dst = os.path.join(DST, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        data = open(src, "rb").read()
        if name.endswith(".http"):
            head, sep, body = data.partition(b"\r\n\r\n")
            lines = head.split(b"\r\n")
            out = [lines[0]]
            for l in lines[1:]:
                k, _, v = l.partition(b":")
                if k.strip().lower().decode() in HDR: l = k + b": <redacted>"
                out.append(l)
            body = re.sub(rb'"request_id":"req_[A-Za-z0-9]+"', b'"request_id":"<redacted>"', body)
            data = b"\r\n".join(out) + sep + body
        open(dst, "wb").write(data)
