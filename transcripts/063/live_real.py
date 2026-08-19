#!/usr/bin/env python3
"""Live-key 307 redirect hunt for issue 063.

Reads ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY from the repo .env.
Writes only redacted transcripts. Unredacted captures stay in /tmp and are
deleted. Rotate the keys after this hunt.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "transcripts/063"
PAIR = OUT / "redirect_pair.py"
BIN = Path(
    os.environ.get(
        "SWITCHYARD_BIN",
        str(ROOT / "tools/switchyard/target/release/switchyard-server"),
    )
)
TMP = Path("/tmp/kairo-063-live")
N = 5


def load_env() -> dict[str, str]:
    secrets: dict[str, str] = {}
    for raw in (ROOT / ".env").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip("'").strip('"')
        if v:
            secrets[k] = v
            os.environ.setdefault(k, v)
    return secrets


def redact(text: str, secrets: dict[str, str]) -> str:
    out = text
    for name, val in secrets.items():
        if val:
            out = out.replace(val, f"REDACTED_{name}")
    return out


def scan(text: str, secrets: dict[str, str]) -> list[str]:
    hits: list[str] = []
    for name, val in secrets.items():
        if not val or len(val) < 8:
            continue
        if val in text:
            hits.append(f"FULL:{name}")
    return hits


def wait_port(port: int, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(0.3)
        ok = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        if ok:
            return
        time.sleep(0.15)
    raise RuntimeError(f"port {port} did not come up")


def http(url: str, payload: dict, extra_headers: dict[str, str] | None = None) -> dict:
    data = json.dumps(payload).encode()
    headers = {"content-type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
            return {"ok": True, "status": resp.status, "body": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"ok": False, "status": e.code, "body": body}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


def kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)


def start(cmd: list[str], extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def write_toml(name: str, body: str) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / f"{name}.toml"
    path.write_text(body)
    return path


def header_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    if not lines:
        return {}
    rec = json.loads(lines[-1])
    return {k.lower(): v for k, v in rec.get("headers", {}).items()}


def run_cell(
    *,
    tag: str,
    origin: int,
    sink: int,
    sy_port: int,
    toml: Path,
    extra_env: dict[str, str],
    url_path: str,
    location_path: str,
    payload: dict,
    extra_headers: dict[str, str] | None,
    secrets: dict[str, str],
) -> tuple[list[dict], str, str]:
    origin_raw = TMP / f"{tag}-origin.jsonl"
    sink_raw = TMP / f"{tag}-sink.jsonl"
    for p in (origin_raw, sink_raw):
        if p.exists():
            p.unlink()
    location = f"http://127.0.0.1:{sink}{location_path}"
    procs = [
        start(["python3", str(PAIR), "sink", str(sink), str(sink_raw)]),
        start(
            [
                "python3",
                str(PAIR),
                "origin",
                str(origin),
                str(origin_raw),
                location,
                "307",
            ]
        ),
    ]
    wait_port(sink)
    wait_port(origin)
    procs.append(
        start(
            [
                str(BIN),
                "--config",
                str(toml),
                "--host",
                "127.0.0.1",
                "-p",
                str(sy_port),
            ],
            extra_env,
        )
    )
    wait_port(sy_port)
    time.sleep(0.2)
    rows: list[dict] = []
    try:
        for run in range(1, N + 1):
            resp = http(
                f"http://127.0.0.1:{sy_port}{url_path}",
                payload,
                extra_headers,
            )
            sink_text = sink_raw.read_text() if sink_raw.exists() else ""
            origin_text = origin_raw.read_text() if origin_raw.exists() else ""
            sink_h = header_map(sink_raw)
            origin_h = header_map(origin_raw)
            row = {
                "tag": f"{tag} r{run}",
                "status": resp["status"],
                "ok": resp["ok"],
                "sink_hits": scan(sink_text, secrets),
                "origin_hits": scan(origin_text, secrets),
                "client_hits": scan(resp.get("body") or "", secrets),
                "sink_has_authorization": "authorization" in sink_h,
                "origin_has_authorization": "authorization" in origin_h,
                "sink_has_x_api_key": "x-api-key" in sink_h,
                "sink_has_x_goog_api_key": "x-goog-api-key" in sink_h,
            }
            rows.append(row)
    finally:
        for proc in procs:
            kill_tree(proc)
        time.sleep(0.2)
    return rows, origin_raw.read_text() if origin_raw.exists() else "", sink_raw.read_text() if sink_raw.exists() else ""


def freeze_redacted(name: str, raw: str, secrets: dict[str, str]) -> None:
    red = redact(raw, secrets)
    leftover = scan(red, secrets)
    if leftover:
        raise SystemExit(f"{name} still contains live secrets: {leftover}")
    (OUT / name).write_text(red)


def main() -> None:
    if not BIN.is_file():
        raise SystemExit(f"missing switchyard-server at {BIN}")
    secrets = load_env()
    needed = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"]
    missing = [k for k in needed if not secrets.get(k)]
    if missing:
        raise SystemExit(f"missing {missing} in .env")
    for k in needed:
        if "REPLACE" in secrets[k] or secrets[k].startswith("CANARY_"):
            raise SystemExit(f"{k} looks like a placeholder, need the live key")

    TMP.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    frozen: dict[str, str] = {}

    anth_toml = write_toml(
        "anth-env",
        "schema_version = 1\n"
        "[llm_clients.anth]\n"
        'format = "anthropic_messages"\n'
        'base_url = "http://127.0.0.1:19420"\n'
        'api_key_env = "ANTHROPIC_API_KEY"\n'
        "[targets.cap]\n"
        'id = "claude-hunt"\n'
        'llm_client = "anth"\n'
        "[routes.primary]\n"
        'id = "claude-hunt"\n'
        'type = "passthrough"\n'
        'target = "cap"\n',
    )
    anth_extra_toml = write_toml(
        "anth-extra",
        "schema_version = 1\n"
        "[llm_clients.anth]\n"
        'format = "anthropic_messages"\n'
        'base_url = "http://127.0.0.1:19450"\n'
        "[llm_clients.anth.extra_headers]\n"
        f'x-api-key = "{secrets["ANTHROPIC_API_KEY"]}"\n'
        "[targets.cap]\n"
        'id = "claude-hunt"\n'
        'llm_client = "anth"\n'
        "[routes.primary]\n"
        'id = "claude-hunt"\n'
        'type = "passthrough"\n'
        'target = "cap"\n',
    )
    oa_toml = write_toml(
        "oa-env",
        "schema_version = 1\n"
        "[llm_clients.oa]\n"
        'format = "openai_chat"\n'
        'base_url = "http://127.0.0.1:19430/v1"\n'
        'api_key_env = "OPENAI_API_KEY"\n'
        "[targets.cap]\n"
        'id = "gpt-hunt"\n'
        'llm_client = "oa"\n'
        "[routes.primary]\n"
        'id = "gpt-hunt"\n'
        'type = "passthrough"\n'
        'target = "cap"\n',
    )
    goog_toml = write_toml(
        "goog-extra",
        "schema_version = 1\n"
        "[llm_clients.g]\n"
        'format = "openai_chat"\n'
        'base_url = "http://127.0.0.1:19440/v1"\n'
        'api_key_env = "OPENAI_API_KEY"\n'
        "[llm_clients.g.extra_headers]\n"
        f'x-goog-api-key = "{secrets["GEMINI_API_KEY"]}"\n'
        "[targets.cap]\n"
        'id = "gemini-hunt"\n'
        'llm_client = "g"\n'
        "[routes.primary]\n"
        'id = "gemini-hunt"\n'
        'type = "passthrough"\n'
        'target = "cap"\n',
    )

    anth_payload = {
        "model": "claude-hunt",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}],
    }
    oa_payload = {
        "model": "gpt-hunt",
        "messages": [{"role": "user", "content": "hi"}],
    }
    goog_payload = {
        "model": "gemini-hunt",
        "messages": [{"role": "user", "content": "hi"}],
    }
    anth_headers = {"anthropic-version": "2023-06-01"}

    cells = [
        (
            "sy_anth_api_key_env",
            19420,
            19421,
            19422,
            anth_toml,
            {},
            "/v1/messages",
            "/v1/messages",
            anth_payload,
            anth_headers,
            "live-anth-api-key-env-origin.jsonl",
            "live-anth-api-key-env-sink.jsonl",
        ),
        (
            "sy_anth_extra_header",
            19450,
            19451,
            19452,
            anth_extra_toml,
            {},
            "/v1/messages",
            "/v1/messages",
            anth_payload,
            anth_headers,
            "live-anth-extra-header-origin.jsonl",
            "live-anth-extra-header-sink.jsonl",
        ),
        (
            "sy_openai_bearer",
            19430,
            19431,
            19432,
            oa_toml,
            {},
            "/v1/chat/completions",
            "/v1/chat/completions",
            oa_payload,
            None,
            "live-openai-bearer-origin.jsonl",
            "live-openai-bearer-sink.jsonl",
        ),
        (
            "sy_goog_extra_header",
            19440,
            19441,
            19442,
            goog_toml,
            {},
            "/v1/chat/completions",
            "/v1/chat/completions",
            goog_payload,
            None,
            "live-goog-extra-header-origin.jsonl",
            "live-goog-extra-header-sink.jsonl",
        ),
    ]

    try:
        for (
            tag,
            origin,
            sink,
            sy_port,
            toml,
            extra_env,
            url_path,
            location_path,
            payload,
            extra_headers,
            origin_name,
            sink_name,
        ) in cells:
            rows, origin_raw, sink_raw = run_cell(
                tag=tag,
                origin=origin,
                sink=sink,
                sy_port=sy_port,
                toml=toml,
                extra_env=extra_env,
                url_path=url_path,
                location_path=location_path,
                payload=payload,
                extra_headers=extra_headers,
                secrets=secrets,
            )
            results.extend(rows)
            freeze_redacted(origin_name, origin_raw, secrets)
            freeze_redacted(sink_name, sink_raw, secrets)
            frozen[tag] = sink_name
    finally:
        if TMP.exists():
            for p in TMP.iterdir():
                p.unlink()
            TMP.rmdir()

    compact = [
        {
            k: row[k]
            for k in (
                "tag",
                "status",
                "ok",
                "sink_hits",
                "origin_hits",
                "client_hits",
                "sink_has_authorization",
                "origin_has_authorization",
                "sink_has_x_api_key",
                "sink_has_x_goog_api_key",
            )
        }
        for row in results
    ]
    (OUT / "live-real-results.json").write_text(json.dumps(results, indent=2) + "\n")
    (OUT / "live-real-scoreboard.json").write_text(json.dumps(compact, indent=2) + "\n")

    leaked = []
    for path in OUT.iterdir():
        if path.is_file():
            leaked.extend(f"{path.name}:{h}" for h in scan(path.read_text(), secrets))
    if leaked:
        raise SystemExit(f"live secrets escaped into transcripts/063: {leaked}")

    print(f"binary {BIN}")
    print(f"repeat {N}")
    by: dict[str, dict] = {}
    for row in results:
        base = row["tag"].rsplit(" r", 1)[0]
        slot = by.setdefault(
            base,
            {"n": 0, "sink": set(), "status": set(), "client": set()},
        )
        slot["n"] += 1
        slot["sink"].update(row["sink_hits"])
        slot["status"].add(row["status"])
        slot["client"].update(row["client_hits"])
    for tag, slot in by.items():
        print(
            f"{tag}\tn={slot['n']}\tstatus={sorted(slot['status'], key=lambda x: (x is None, x))}"
            f"\tsink={sorted(slot['sink'])}\tclient={sorted(slot['client'])}"
        )


if __name__ == "__main__":
    main()
