"""Export sanitized Codex consumer-boundary evidence for issue 084.

Reads the raw run directories written by tap.py and run_case.sh and writes the
committed evidence set. Only /v1/alpha/search exchanges are copied in full.
Codex /responses bodies and full stderr are summarized, not copied, because
they carry local environment content (skill paths and files Codex read).

    python3 transcripts/084/codex/export.py --runs /tmp/k084c/runs --out transcripts/084/codex/runs
"""
import argparse, gzip, json, pathlib, re, shutil

p = argparse.ArgumentParser()
p.add_argument("--runs", type=pathlib.Path, required=True)
p.add_argument("--out", type=pathlib.Path, required=True)
a = p.parse_args()

REDACT_HEADERS = {
    "authorization", "api-key", "cookie", "set-cookie",
    "openai-organization", "openai-project",
    "llm_provider-openai-organization", "llm_provider-openai-project",
    "x-codex-turn-metadata", "session-id", "thread-id", "x-codex-window-id",
}
HOME = str(pathlib.Path.home())
KEEP_STDERR = re.compile(r"^(OpenAI Codex v|model:|provider:|web search:|warning: Falling back)|tools::router")


def scrub(text):
    return text.replace(HOME, "~")


def headers(h):
    return {k: ("[REDACTED]" if k.lower() in REDACT_HEADERS else v) for k, v in h.items()}


def body(path, meta):
    raw = path.read_bytes()
    if meta.get("headers", {}).get("content-encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw


if a.out.exists():
    shutil.rmtree(a.out)
summary = {}
for run in sorted(d for d in a.runs.iterdir() if d.is_dir() and (d / "exit.txt").exists()):
    out = a.out / run.name
    (out / "search").mkdir(parents=True)
    exchanges, searches, web_run_offered = [], {}, None
    for side in ("client", "upstream", "direct"):
        for req_meta_path in sorted((run / side).glob("*-request.json")) if (run / side).exists() else []:
            stem = req_meta_path.name[: -len("-request.json")]
            req = json.loads(req_meta_path.read_text())
            resp = json.loads((run / side / f"{stem}-response.json").read_text())
            exchanges.append({"side": side, "n": req["n"], "method": req["method"], "path": req["path"], "status": resp["status"]})
            req_body = (run / side / f"{stem}-request-body.bin").read_bytes()
            if req["path"].endswith("/responses") and side != "upstream" and web_run_offered is None:
                web_run_offered = b"web.run" in req_body
            if not req["path"].endswith("/alpha/search"):
                continue
            k = len(searches.setdefault(side, []))
            name = f"{side}-{k + 1:02d}"
            (out / "search" / f"{name}-request.json").write_text(json.dumps(
                {"method": req["method"], "path": req["path"], "headers": headers(req["headers"])}, indent=2) + "\n")
            (out / "search" / f"{name}-request-body.json").write_bytes(req_body)
            (out / "search" / f"{name}-response.json").write_text(json.dumps(
                {"status": resp["status"], "headers": headers(resp["headers"])}, indent=2) + "\n")
            (out / "search" / f"{name}-response-body.json").write_bytes(body(run / side / f"{stem}-response-body.bin", resp))
            searches[side].append({"status": resp["status"], "has_id": "id" in json.loads(req_body)})
    (out / "exchanges.json").write_text(json.dumps(exchanges, indent=2) + "\n")
    stdout = scrub((run / "stdout.txt").read_text())
    (out / "stdout.txt").write_text(stdout)
    kept = [scrub(l) for l in (run / "stderr.txt").read_text().splitlines() if KEEP_STDERR.search(l)]
    (out / "stderr-search-lines.txt").write_text("\n".join(kept) + "\n")
    (out / "codex-args.txt").write_text(scrub((run / "codex-args.txt").read_text()) + "\n")
    summary[run.name] = {
        "codex_exit": (run / "exit.txt").read_text().strip(),
        "web_run_tool_offered": web_run_offered,
        "search_calls": {s: {"count": len(v), "with_id": sum(x["has_id"] for x in v),
                             "statuses": sorted({x["status"] for x in v})} for s, v in searches.items()},
        "final_answer": stdout.strip().splitlines()[-1] if stdout.strip() else "",
    }
(a.out / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
