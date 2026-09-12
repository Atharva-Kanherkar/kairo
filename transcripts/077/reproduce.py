#!/usr/bin/env python3
"""Reproduce issue 077 through Bifrost's public Responses endpoint.

The capture upstream is deterministic because the claim concerns only Bifrost's
header forwarding. The runner uses a source-pinned, locally built Bifrost binary,
enables virtual-key authentication, records literal HTTP bytes, and performs five
authenticated trials plus five unauthenticated controls.

Reviewer run:
    BIFROST_SOURCE=/path/to/bifrost BIFROST_BIN=/path/to/bifrost-http \
        python3 transcripts/077/reproduce.py

Maintainer fixture refresh:
    BIFROST_SOURCE=/path/to/bifrost BIFROST_BIN=/path/to/bifrost-http \
        python3 transcripts/077/reproduce.py --output-dir transcripts/077
"""

import argparse
import http.client
import io
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
from datetime import datetime

HOST = "127.0.0.1"
PINNED_COMMIT = "44a562431ee0463cb1afe0e5833cde07d7921701"
EXPECTED_VERSION = "1.8.6-44a562431e"
RUNS = 5
READY_TIMEOUT_SECONDS = 60

VIRTUAL_KEY = "sk-bf-vk-dummy-not-a-real-credential-077"
PROVIDER_KEY = "dummy-provider-key-not-a-real-credential-077"
SECRET = "CANARY_ADMIN_ONLY_PROVIDER_SECRET_077"
AUTHORIZATION_CONTROL = "CANARY_AUTHORIZATION_CONTROL_077"
SAFE_TRACE = "CANARY_SAFE_TRACE_077"

RELEVANT_SOURCE_PATHS = (
    "core/providers/utils/utils.go",
    "core/schemas/provider.go",
    "core/schemas/trace.go",
    "transports/bifrost-http/handlers/inference.go",
)


class ReproductionError(Exception):
    """A reproduction failure whose message contains no live credential."""


def require(condition, message):
    if not condition:
        raise ReproductionError(message)


def free_port():
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def parse_http_response(raw):
    class CapturedSocket:
        def makefile(self, mode):
            return io.BytesIO(raw)

    try:
        response = http.client.HTTPResponse(CapturedSocket())
        response.begin()
        status = response.status
        headers = list(response.getheaders())
        body = response.read()
        return status, headers, body
    except (http.client.HTTPException, ValueError) as exc:
        raise ReproductionError("malformed or truncated HTTP response") from exc


def header_values(headers):
    result = {}
    for name, value in headers:
        result.setdefault(name.lower(), []).append(value)
    return result


def raw_request(port, path, body, virtual_key=None, timeout=10):
    lines = [
        f"POST {path} HTTP/1.1",
        f"Host: {HOST}:{port}",
        "Content-Type: application/json",
        f"Content-Length: {len(body)}",
    ]
    if virtual_key is not None:
        lines.append(f"x-bf-vk: {virtual_key}")
    lines.append("Connection: close")
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body
    with socket.create_connection((HOST, port), timeout=timeout) as sock:
        sock.sendall(request)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response = b"".join(chunks)
    status, headers, response_body = parse_http_response(response)
    return request, response, status, headers, response_body


def raw_get(port, path, timeout=5):
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {HOST}:{port}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection((HOST, port), timeout=timeout) as sock:
        sock.sendall(request)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response = b"".join(chunks)
    status, headers, body = parse_http_response(response)
    return request, response, status, headers, body


def read_one_request(stream):
    request_line = stream.readline()
    if not request_line:
        return b"", "", {}, b""
    header_lines = []
    while True:
        line = stream.readline()
        if not line:
            raise ReproductionError("capture upstream received truncated headers")
        header_lines.append(line)
        if line in (b"\r\n", b"\n"):
            break
    headers = {}
    for line in header_lines[:-1]:
        name, separator, value = line.partition(b":")
        require(separator == b":", "capture upstream received malformed header")
        headers[name.decode("latin-1").lower()] = value.strip().decode("latin-1")
    content_length = int(headers.get("content-length", "0"))
    body = stream.read(content_length)
    require(len(body) == content_length, "capture upstream received truncated body")
    raw = request_line + b"".join(header_lines) + body
    path = request_line.decode("latin-1").split(" ", 2)[1]
    return raw, path, headers, body


class CaptureHandler(socketserver.StreamRequestHandler):
    def handle(self):
        raw, path, headers, request_body = read_one_request(self.rfile)
        if not raw:
            return

        if path.endswith("/v1/models") or path.endswith("/models"):
            body = json.dumps(
                {"object": "list", "data": [{"id": "mock-model", "object": "model"}]},
                separators=(",", ":"),
            ).encode()
        else:
            try:
                parsed = json.loads(request_body.decode())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ReproductionError("Bifrost forwarded malformed JSON") from exc
            require(parsed.get("model") == "mock-model", "unexpected forwarded model")
            body = json.dumps(
                {
                    "id": "resp_077",
                    "object": "response",
                    "created_at": 1789250000,
                    "status": "completed",
                    "model": "mock-model",
                    "output": [
                        {
                            "id": "msg_077",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "synthetic-ok",
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
                separators=(",", ":"),
            ).encode()

        response_headers = [
            b"HTTP/1.1 200 OK",
            b"Content-Type: application/json",
            f"Content-Length: {len(body)}".encode(),
            f"X-Provider-Secret: {headers.get('x-provider-secret', 'MISSING')}".encode(),
            f"X-Safe-Trace: {headers.get('x-safe-trace', 'MISSING')}".encode(),
            f"Authorization: Bearer {AUTHORIZATION_CONTROL}".encode(),
            b"Connection: close",
        ]
        response = b"\r\n".join(response_headers) + b"\r\n\r\n" + body
        self.request.sendall(response)

        if not path.endswith("/models"):
            with self.server.capture_lock:
                self.server.exchanges.append((raw, response))


class CaptureServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, CaptureHandler)
        self.capture_lock = threading.Lock()
        self.exchanges = []


def sanitize_client_request(raw):
    return re.sub(
        rb"(?im)^(x-bf-vk:\s*)[^\r\n]+",
        rb"\1<VIRTUAL_KEY>",
        raw,
    )


def sanitize_upstream_request(raw):
    return re.sub(
        rb"(?im)^(authorization:\s*bearer\s+)[^\r\n]+",
        rb"\1<BIFROST_PROVIDER_AUTH>",
        raw,
    )


def check_source(source):
    require((source / ".git").exists(), "BIFROST_SOURCE is not a Git checkout")
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    require(revision == PINNED_COMMIT, f"expected Bifrost {PINNED_COMMIT}, found {revision}")
    diff = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *RELEVANT_SOURCE_PATHS],
        cwd=source,
        check=False,
    )
    require(diff.returncode == 0, "relevant Bifrost source files have local changes")
    core_version = (source / "core/version").read_text().strip()
    require(core_version == "1.8.6", f"expected core 1.8.6, found {core_version}")


def check_binary(binary):
    build_info = subprocess.check_output(
        ["go", "version", "-m", str(binary)], text=True
    )
    require(
        "path\tgithub.com/maximhq/bifrost/transports/bifrost-http" in build_info,
        "BIFROST_BIN is not the official Bifrost HTTP transport package",
    )
    require(
        f"build\tvcs.revision={PINNED_COMMIT}" in build_info,
        "BIFROST_BIN was not built from the pinned Bifrost revision",
    )
    require(
        f"-X main.Version={EXPECTED_VERSION}" in build_info,
        "BIFROST_BIN lacks the expected version linker setting",
    )
    modified = "build\tvcs.modified=true" in build_info
    return {"vcs_revision": PINNED_COMMIT, "vcs_modified": modified}


def make_config(app_dir, upstream_port):
    return {
        "$schema": "https://www.getbifrost.ai/schema",
        "client": {
            "enable_logging": False,
            "enforce_auth_on_inference": True,
            "initial_pool_size": 8,
        },
        "config_store": {
            "enabled": True,
            "type": "sqlite",
            "config": {"path": str(app_dir / "config.db")},
        },
        "logs_store": {"enabled": False},
        "governance": {
            "auth_config": {"is_enabled": False},
            "virtual_keys": [
                {
                    "id": "vk-077-low-privilege-caller",
                    "name": "low-privilege-caller",
                    "value": VIRTUAL_KEY,
                    "is_active": True,
                    "provider_configs": [
                        {
                            "provider": "mockoai",
                            "allowed_models": ["*"],
                            "key_ids": ["mock-key"],
                            "weight": 1,
                        }
                    ],
                }
            ],
        },
        "providers": {
            "mockoai": {
                "keys": [
                    {
                        "id": "mock-key",
                        "name": "mock-key",
                        "value": PROVIDER_KEY,
                        "weight": 1,
                        "models": ["*"],
                    }
                ],
                "network_config": {
                    "base_url": f"http://{HOST}:{upstream_port}",
                    "allow_private_network": True,
                    "extra_headers": {
                        "X-Provider-Secret": SECRET,
                        "X-Safe-Trace": SAFE_TRACE,
                    },
                },
                "custom_provider_config": {"base_provider_type": "openai"},
            }
        },
    }


def wait_for_ready(port, process, log_path):
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        code = process.poll()
        require(code is None, f"Bifrost exited during startup with code {code}")
        try:
            _, _, status, _, body = raw_get(port, "/api/version")
            if status == 200:
                version = json.loads(body.decode())
                require(version == EXPECTED_VERSION, f"expected binary {EXPECTED_VERSION}, found {version}")
                return version
        except (OSError, ReproductionError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    diagnostic = Path(log_path).read_bytes()[-4096:]
    diagnostic = diagnostic.replace(VIRTUAL_KEY.encode(), b"<VIRTUAL_KEY>")
    diagnostic = diagnostic.replace(PROVIDER_KEY.encode(), b"<PROVIDER_KEY>")
    raise ReproductionError(
        "Bifrost did not become ready; sanitized log tail:\n"
        + diagnostic.decode(errors="replace")
    )


def validate_authenticated(response, status, headers, body):
    require(status == 200, f"authenticated request returned HTTP {status}")
    values = header_values(headers)
    require(values.get("x-provider-secret") == [SECRET], "secret missing from HTTP response header")
    require("authorization" not in values, "Authorization control leaked in HTTP response header")
    require(values.get("x-safe-trace") == [SAFE_TRACE], "benign response header was not preserved")
    try:
        parsed = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReproductionError("authenticated client response is not valid JSON") from exc
    provider_headers = parsed.get("extra_fields", {}).get("provider_response_headers", {})
    require(
        provider_headers.get("X-Provider-Secret") == SECRET,
        "secret missing from provider_response_headers JSON metadata",
    )
    require(
        not any(name.lower() == "authorization" for name in provider_headers),
        "Authorization control leaked in JSON metadata",
    )
    require(
        provider_headers.get("X-Safe-Trace") == SAFE_TRACE,
        "benign header missing from JSON metadata",
    )
    require(SECRET in response.decode("latin-1"), "consumer could not extract secret marker")
    return parsed


def validate_unauthenticated(response, status):
    require(status == 401, f"unauthenticated control returned HTTP {status}")
    require(SECRET.encode() not in response, "unauthenticated response exposed secret marker")


def expected_exchange(client_request, parsed_body):
    expected_body = json.loads(json.dumps(parsed_body))
    response_headers = expected_body.get("extra_fields", {}).get(
        "provider_response_headers", {}
    )
    response_headers.pop("X-Provider-Secret", None)
    body = json.dumps(expected_body, separators=(",", ":")).encode()
    response = (
        b"HTTP/1.1 200 OK\r\n"
        + b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + f"X-Safe-Trace: {SAFE_TRACE}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )
    return sanitize_client_request(client_request) + response


def write_artifacts(output_dir, artifacts):
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in artifacts.items():
        path = output_dir / name
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run(binary, source, output_dir):
    check_source(source)
    require(binary.is_file(), f"BIFROST_BIN does not exist: {binary}")
    require(os.access(binary, os.X_OK), f"BIFROST_BIN is not executable: {binary}")
    binary_provenance = check_binary(binary)

    bifrost_port = free_port()
    upstream_port = free_port()
    request_body = json.dumps(
        {
            "model": "mockoai/mock-model",
            "input": "Return the deterministic fixture.",
            "max_output_tokens": 16,
        },
        separators=(",", ":"),
    ).encode()

    capture_server = CaptureServer((HOST, upstream_port))
    capture_thread = threading.Thread(target=capture_server.serve_forever, daemon=True)
    capture_thread.start()
    process = None
    log_file = None
    try:
        with tempfile.TemporaryDirectory(prefix="kairo-077-bifrost-") as app_dir_text:
            app_dir = Path(app_dir_text)
            (app_dir / "config.json").write_text(
                json.dumps(make_config(app_dir, upstream_port), indent=2) + "\n"
            )
            log_file = tempfile.NamedTemporaryFile(prefix="kairo-077-log-", delete=False)
            log_path = log_file.name
            environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            }
            process = subprocess.Popen(
                [
                    str(binary),
                    "-app-dir",
                    str(app_dir),
                    "-host",
                    HOST,
                    "-port",
                    str(bifrost_port),
                    "-log-level",
                    "error",
                ],
                cwd=app_dir,
                env=environment,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            version = wait_for_ready(bifrost_port, process, log_path)

            observed = None
            expected = None
            unauthenticated = None
            for _ in range(RUNS):
                client_request, client_response, status, headers, body = raw_request(
                    bifrost_port,
                    "/v1/responses",
                    request_body,
                    virtual_key=VIRTUAL_KEY,
                )
                parsed = validate_authenticated(client_response, status, headers, body)
                if observed is None:
                    observed = sanitize_client_request(client_request) + client_response
                    expected = expected_exchange(client_request, parsed)

                control_request, control_response, status, _, _ = raw_request(
                    bifrost_port, "/v1/responses", request_body
                )
                validate_unauthenticated(control_response, status)
                if unauthenticated is None:
                    unauthenticated = control_request + control_response

            with capture_server.capture_lock:
                exchanges = list(capture_server.exchanges)
            require(len(exchanges) == RUNS, f"capture upstream recorded {len(exchanges)}/{RUNS} requests")
            for forwarded_request, upstream_response in exchanges:
                require(
                    f"X-Provider-Secret: {SECRET}".encode() in forwarded_request,
                    "configured provider secret did not reach capture upstream",
                )
                require(
                    f"X-Safe-Trace: {SAFE_TRACE}".encode() in forwarded_request,
                    "configured safe header did not reach capture upstream",
                )
                require(
                    re.search(rb"(?im)^authorization:\s*bearer\s+", forwarded_request),
                    "provider Authorization was not set upstream",
                )
                require(
                    f"Authorization: Bearer {AUTHORIZATION_CONTROL}".encode()
                    in upstream_response,
                    "capture upstream did not emit Authorization control",
                )

            first_upstream = (
                sanitize_upstream_request(exchanges[0][0]) + exchanges[0][1]
            )
            results = {
                "complete": True,
                "date_checked": datetime.now().astimezone().date().isoformat(),
                "target": {
                    "repository": "https://github.com/maximhq/bifrost",
                    "commit": PINNED_COMMIT,
                    "core_version": "1.8.6",
                    "binary_version": version,
                    "binary_vcs_revision": binary_provenance["vcs_revision"],
                    "binary_vcs_modified": binary_provenance["vcs_modified"],
                    "endpoint": "/v1/responses",
                    "client_dialect": "OpenAI Responses",
                    "backend_dialect": "OpenAI Responses",
                    "provider": "custom OpenAI-compatible capture upstream",
                    "model": "mock-model",
                },
                "runs": RUNS,
                "authenticated": {
                    "http_200": RUNS,
                    "secret_in_http_header": RUNS,
                    "secret_in_json_metadata": RUNS,
                    "consumer_extracted_admin_secret": RUNS,
                },
                "same_response_controls": {
                    "authorization_stripped_from_header_and_json": RUNS,
                    "safe_trace_preserved_in_header_and_json": RUNS,
                },
                "unauthenticated_control": {
                    "http_401": RUNS,
                    "secret_absent": RUNS,
                },
                "secrets": "synthetic canaries only; no provider credential was used",
            }
            artifacts = {
                "observed.http": observed,
                "expected.http": expected,
                "unauthenticated-control.http": unauthenticated,
                "upstream.http": first_upstream,
                "results.json": results,
            }
            write_artifacts(output_dir, artifacts)
            return results
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if log_file is not None:
            log_path = log_file.name
            log_file.close()
            try:
                os.unlink(log_path)
            except FileNotFoundError:
                pass
        capture_server.shutdown()
        capture_server.server_close()
        capture_thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="artifact directory; defaults to a fresh ignored temporary directory",
    )
    parser.add_argument("--binary", type=Path, default=os.environ.get("BIFROST_BIN"))
    parser.add_argument("--source", type=Path, default=os.environ.get("BIFROST_SOURCE"))
    args = parser.parse_args()
    require(args.binary is not None, "set BIFROST_BIN or pass --binary")
    require(args.source is not None, "set BIFROST_SOURCE or pass --source")
    output_dir = args.output_dir
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="kairo-077-review-"))
    results = run(args.binary.resolve(), args.source.resolve(), output_dir.resolve())
    print(json.dumps(results, indent=2, sort_keys=True))
    print(f"artifacts: {output_dir.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.SubprocessError, ReproductionError) as exc:
        print(f"reproduction failed: {exc}", file=os.sys.stderr)
        raise SystemExit(1)
