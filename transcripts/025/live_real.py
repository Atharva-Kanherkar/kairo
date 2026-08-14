#!/usr/bin/env python3
"""Live real-key evidence. Writes only redacted transcripts. Rotate keys after."""
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
OUT025 = ROOT / "transcripts/025"
OUT024 = ROOT / "transcripts/024"
TMP = Path("/tmp/kairo-live-keys")
N = 5


def load_env() -> dict[str, str]:
    secrets: dict[str, str] = {}
    env_path = ROOT / ".env"
    for raw in env_path.read_text().splitlines():
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
        elif val[:4] in text and val[-4:] in text:
            hits.append(f"PREFIX_SUFFIX:{name}")
    return hits


def wait_port(port: int, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(0.3)
        ok = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        if ok:
            return
        time.sleep(0.2)
    raise RuntimeError(f"port {port} did not come up")


def http(method: str, url: str, payload: dict | None = None, timeout: float = 45) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return {"ok": True, "status": resp.status, "body": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"ok": False, "status": e.code, "body": body}
    except Exception as e:
        return {"ok": False, "status": None, "body": f"{type(e).__name__}: {e}"}


def summarize(tag: str, resp: dict, secrets: dict[str, str]) -> dict:
    body = resp.get("body") or ""
    hits = scan(body, secrets)
    snippet = redact(body, secrets)
    if len(snippet) > 2000:
        snippet = snippet[:2000] + "...truncated..."
    return {
        "tag": tag,
        "status": resp.get("status"),
        "ok": resp.get("ok"),
        "hits": hits,
        "body": snippet,
    }


def chat(model: str, tag: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": tag}],
        "max_tokens": 8,
    }


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


def write_ll_yaml(secrets: dict[str, str]) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / "litellm-extra-headers.yaml"
    gemini = secrets["GEMINI_API_KEY"]
    path.write_text(
        "model_list:\n"
        "  - model_name: gemini-flash\n"
        "    litellm_params:\n"
        "      model: gemini/gemini-2.5-flash\n"
        "      api_key: os.environ/GEMINI_API_KEY\n"
        "      extra_headers:\n"
        f"        x-goog-api-key: {gemini}\n"
        "      aws_session_token: CANARY_NOT_A_CLOUD_TOKEN\n"
    )
    return path


def write_sy_toml(name: str, fmt: str, base_url: str, key_env: str, model: str, retries: int = 2) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / f"sy-{name}.toml"
    path.write_text(
        "schema_version = 1\n"
        f"[llm_clients.c]\nformat = \"{fmt}\"\n"
        f"base_url = \"{base_url}\"\n"
        f"api_key_env = \"{key_env}\"\n"
        f"max_retries = {retries}\n"
        f"[targets.t]\nid = \"{model}\"\nllm_client = \"c\"\n"
        f"[routes.primary]\nid = \"{model}\"\ntype = \"passthrough\"\ntarget = \"t\"\n"
    )
    return path


def write_sy_query(name: str, key: str) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / f"sy-query-{name}.toml"
    # Key is in the URL on purpose. File lives in /tmp and is deleted.
    path.write_text(
        "schema_version = 1\n"
        "[llm_clients.c]\nformat = \"openai_chat\"\n"
        f"base_url = \"http://127.0.0.1:19998/v1?key={key}\"\n"
        "max_retries = 0\n"
        "[targets.t]\nid = \"captured-model\"\nllm_client = \"c\"\n"
        "[routes.primary]\nid = \"captured-model\"\ntype = \"passthrough\"\ntarget = \"t\"\n"
    )
    return path


def main() -> None:
    secrets = load_env()
    needed = ["GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"]
    missing = [k for k in needed if not secrets.get(k)]
    if missing:
        raise SystemExit(f"missing {missing}")

    results: list[dict] = []
    procs: list[subprocess.Popen] = []
    try:
        ll_yaml = write_ll_yaml(secrets)
        ll = start(
            [
                str(ROOT / "tools/litellm-env/bin/python"),
                str(ROOT / "tools/litellm-env/bin/litellm"),
                "--config",
                str(ll_yaml),
                "--port",
                "4001",
                "--host",
                "127.0.0.1",
            ]
        )
        procs.append(ll)
        wait_port(4001, 90)
        # liveliness can come up before routes; give the proxy a beat
        time.sleep(1)

        for run in range(1, N + 1):
            results.append(
                summarize(
                    f"ll_health r{run}",
                    http("GET", "http://127.0.0.1:4001/health", timeout=60),
                    secrets,
                )
            )
            results.append(
                summarize(
                    f"ll_model_info r{run}",
                    http("GET", "http://127.0.0.1:4001/model/info"),
                    secrets,
                )
            )
            results.append(
                summarize(
                    f"ll_models r{run}",
                    http("GET", "http://127.0.0.1:4001/v1/models"),
                    secrets,
                )
            )
            results.append(
                summarize(
                    f"ll_chat r{run}",
                    http(
                        "POST",
                        "http://127.0.0.1:4001/v1/chat/completions",
                        chat("gemini-flash", f"live-ll {run}"),
                    ),
                    secrets,
                )
            )

        providers = [
            (
                "openai",
                "openai_chat",
                "https://api.openai.com/v1",
                "OPENAI_API_KEY",
                "gpt-4o-mini",
                9000,
            ),
            (
                "gemini",
                "openai_chat",
                "https://generativelanguage.googleapis.com/v1beta/openai",
                "GEMINI_API_KEY",
                "gemini-2.5-flash",
                9001,
            ),
            (
                "anthropic",
                "anthropic_messages",
                "https://api.anthropic.com",
                "ANTHROPIC_API_KEY",
                "claude-3-5-haiku-20241022",
                9002,
            ),
            (
                "openrouter",
                "openai_chat",
                "https://openrouter.ai/api/v1",
                "OPENROUTER_API_KEY",
                "openai/gpt-4o-mini",
                9003,
            ),
        ]

        for name, fmt, base, key_env, model, port in providers:
            toml = write_sy_toml(name, fmt, base, key_env, model)
            proc = start(
                [
                    str(ROOT / "tools/switchyard/target/release/switchyard-server"),
                    "--config",
                    str(toml),
                    "--port",
                    str(port),
                ]
            )
            procs.append(proc)
            wait_port(port, 15)
            url = f"http://127.0.0.1:{port}/v1/chat/completions"
            if fmt == "anthropic_messages":
                payload_fn = lambda run: {
                    "model": model,
                    "max_tokens": 8,
                    "messages": [{"role": "user", "content": f"live-sy {run}"}],
                }
                url = f"http://127.0.0.1:{port}/v1/messages"
            else:
                payload_fn = lambda run, m=model: chat(m, f"live-sy {run}")
            for run in range(1, N + 1):
                results.append(
                    summarize(
                        f"sy_{name}_chat r{run}",
                        http("POST", url, payload_fn(run)),
                        secrets,
                    )
                )

        # Real keys in ?key= on a closed port: the 502 must contain the live secret.
        for name, key_name in [
            ("gemini", "GEMINI_API_KEY"),
            ("openai", "OPENAI_API_KEY"),
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("openrouter", "OPENROUTER_API_KEY"),
        ]:
            toml = write_sy_query(name, secrets[key_name])
            proc = start(
                [
                    str(ROOT / "tools/switchyard/target/release/switchyard-server"),
                    "--config",
                    str(toml),
                    "--port",
                    "9004",
                ]
            )
            wait_port(9004, 15)
            for run in range(1, N + 1):
                results.append(
                    summarize(
                        f"sy_502_{name} r{run}",
                        http(
                            "POST",
                            "http://127.0.0.1:9004/v1/chat/completions",
                            chat("captured-model", f"502 {name} {run}"),
                            timeout=20,
                        ),
                        secrets,
                    )
                )
            kill_tree(proc)
            time.sleep(0.3)

    finally:
        for proc in procs:
            kill_tree(proc)
        if TMP.exists():
            for p in TMP.iterdir():
                p.unlink()
            TMP.rmdir()

    OUT025.mkdir(parents=True, exist_ok=True)
    OUT024.mkdir(parents=True, exist_ok=True)
    compact = [{k: row[k] for k in ("tag", "status", "ok", "hits")} for row in results]
    (OUT025 / "live-real-results.json").write_text(json.dumps(results, indent=2) + "\n")
    (OUT025 / "live-real-scoreboard.json").write_text(json.dumps(compact, indent=2) + "\n")

    # Keep one redacted /health body for 024 if present
    for row in results:
        if row["tag"] == "ll_health r1" and row["body"].startswith("{"):
            try:
                (OUT024 / "live-health-redacted.json").write_text(
                    json.dumps(json.loads(row["body"]), indent=2) + "\n"
                )
            except json.JSONDecodeError:
                (OUT024 / "live-health-redacted.json").write_text(row["body"] + "\n")
            break
    for row in results:
        if row["tag"] == "sy_502_gemini r1" and row["body"].startswith("{"):
            try:
                (OUT025 / "live-502-gemini-redacted.json").write_text(
                    json.dumps(json.loads(row["body"]), indent=2) + "\n"
                )
            except json.JSONDecodeError:
                (OUT025 / "live-502-gemini-redacted.json").write_text(row["body"] + "\n")
            break

    by: dict[str, dict] = {}
    for row in results:
        base = row["tag"].rsplit(" r", 1)[0]
        slot = by.setdefault(base, {"n": 0, "hits": set(), "statuses": set()})
        slot["n"] += 1
        slot["hits"].update(row["hits"])
        slot["statuses"].add(row["status"])
    print("repeat", N)
    for tag, slot in by.items():
        print(
            f"{tag}\tn={slot['n']}\tstatus={sorted(slot['statuses'], key=lambda x: (x is None, x))}\thits={sorted(slot['hits'])}"
        )


if __name__ == "__main__":
    main()
