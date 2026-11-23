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
`custom_tool_call_output`, and Anthropic `tool_result` blocks converted via
`ToBifrostResponsesRequest`) populate `ResponsesToolMessage` with `Content == nil`,
any virtual key can reference another virtual key's private `file_id` inside a
tool output and Bifrost forwards it upstream with the shared provider key.

Offline deterministic run (stateful PDF-parsing provider server):
    BIFROST_SOURCE=/path/to/bifrost BIFROST_BIN=/path/to/bifrost-http \
        python3 transcripts/148/reproduce.py

Live OpenAI run (forwards through recording relay to https://api.openai.com):
    OPENAI_API_KEY=... BIFROST_SOURCE=/path/to/bifrost BIFROST_BIN=/path/to/bifrost-http \
        python3 transcripts/148/reproduce.py --live-openai
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
import urllib.error
import urllib.request
from datetime import datetime

HOST = "127.0.0.1"
PINNED_COMMIT = "783c682951a1c4d6a07a677779e0becab8bbbe2c"
EXPECTED_CORE_VERSION = "1.16.0"
EXPECTED_VERSION = "1.16.0-783c682951"
RUNS = 5
READY_TIMEOUT_SECONDS = 60

VK_TENANT_A = "sk-bf-vk-tenant-a-dummy-not-a-real-credential-148"
VK_TENANT_B = "sk-bf-vk-tenant-b-dummy-not-a-real-credential-148"
SYNTHETIC_PROVIDER_KEY = "sk-proj-dummy-shared-openai-provider-key-148"
SYNTHETIC_FILE_ID = "file-tenant-a-secret-148"
TENANT_A_PDF_CANARY = "KAIRO-148-PRIVATE-PDF-CANARY-94827"

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


def make_valid_pdf(text):
    """Build a valid, spec-compliant PDF 1.4 document with text in a content stream."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii")
        + stream
        + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(out)


def parse_pdf_text_streams(pdf_bytes):
    """Extract literal text strings from PDF Tj operators in a valid PDF 1.4 file."""
    if not pdf_bytes.startswith(b"%PDF-") or b"%%EOF" not in pdf_bytes:
        raise ValueError("uploaded bytes are not a valid PDF document")
    matches = re.findall(rb"\(([^()]+)\)\s*Tj", pdf_bytes)
    if not matches:
        raise ValueError("PDF contains no extractable text stream")
    return " ".join(m.decode("latin-1") for m in matches)


def parse_multipart_upload(content_type, body):
    """Extract purpose, filename, part content-type, and raw file bytes from multipart/form-data."""
    m = re.search(r"boundary=([^\s;]+)", content_type)
    if not m:
        raise ValueError("missing multipart boundary")
    boundary = m.group(1).strip('"').encode("latin-1")
    delimiter = b"--" + boundary
    purpose = None
    filename = None
    part_ct = None
    file_bytes = None
    for part in body.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head_bytes, sep, payload = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        head_text = head_bytes.decode("latin-1")
        if 'name="purpose"' in head_text:
            purpose = payload.decode("latin-1").strip()
        elif 'name="file"' in head_text:
            fn_match = re.search(r'filename="([^"]+)"', head_text)
            filename = fn_match.group(1) if fn_match else "upload.bin"
            ct_match = re.search(r"(?im)^content-type:\s*([^\r\n]+)", head_text)
            part_ct = ct_match.group(1).strip() if ct_match else "application/octet-stream"
            file_bytes = payload
    if not purpose or file_bytes is None:
        raise ValueError("multipart upload missing purpose or file field")
    return purpose, filename, part_ct, file_bytes


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
    timeout=60,
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
    header_part = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="purpose"\r\n\r\n'
        f"{purpose}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("ascii")
    footer_part = f"\r\n--{boundary}--\r\n".encode("ascii")
    body = header_part + file_bytes + footer_part
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


def validate_and_extract_responses_file_ids(parsed):
    """Validate tool-call pairing in a Responses API request and return referenced file_ids."""
    input_items = parsed.get("input")
    if not isinstance(input_items, list):
        return [], None

    seen_fn_calls = set()
    seen_custom_calls = set()
    referenced_ids = []

    for item in input_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "message")
        if item_type == "function_call":
            cid = item.get("call_id")
            if cid:
                seen_fn_calls.add(cid)
        elif item_type == "custom_tool_call":
            cid = item.get("call_id")
            if cid:
                seen_custom_calls.add(cid)
        elif item_type == "function_call_output":
            cid = item.get("call_id")
            if not cid or cid not in seen_fn_calls:
                return None, f"No tool call found for function call output with call_id {cid}."
            output = item.get("output")
            if isinstance(output, list):
                for block in output:
                    if isinstance(block, dict) and block.get("file_id"):
                        referenced_ids.append(block["file_id"])
        elif item_type == "custom_tool_call_output":
            cid = item.get("call_id")
            if not cid or cid not in seen_custom_calls:
                return None, f"No tool call found for custom tool call output with call_id {cid}."
            output = item.get("output")
            if isinstance(output, list):
                for block in output:
                    if isinstance(block, dict) and block.get("file_id"):
                        referenced_ids.append(block["file_id"])
        elif item_type == "message":
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("file_id"):
                        referenced_ids.append(block["file_id"])

    return referenced_ids, None


class CaptureHandler(socketserver.StreamRequestHandler):
    def handle(self):
        raw, method, path, headers, request_body = read_one_request(self.rfile)
        if not raw:
            return

        if self.server.live_openai:
            self.handle_live_openai(raw, method, path, headers, request_body)
            return

        auth = headers.get("authorization", "")
        status_line = b"HTTP/1.1 200 OK"

        if path.endswith("/v1/models") or path.endswith("/models"):
            body = json.dumps(
                {"object": "list", "data": [{"id": "gpt-4o-mini", "object": "model"}]},
                separators=(",", ":"),
            ).encode()
        elif method == "POST" and path.endswith("/v1/files"):
            try:
                purpose, filename, part_ct, file_bytes = parse_multipart_upload(
                    headers.get("content-type", ""), request_body
                )
                extracted_text = parse_pdf_text_streams(file_bytes)
            except ValueError as exc:
                status_line = b"HTTP/1.1 400 Bad Request"
                body = json.dumps(
                    {"error": {"message": str(exc), "type": "invalid_request_error"}},
                    separators=(",", ":"),
                ).encode()
            else:
                with self.server.capture_lock:
                    self.server.uploaded_files[(auth, SYNTHETIC_FILE_ID)] = {
                        "id": SYNTHETIC_FILE_ID,
                        "purpose": purpose,
                        "filename": filename,
                        "content_type": part_ct,
                        "bytes": file_bytes,
                        "extracted_text": extracted_text,
                    }
                body = json.dumps(
                    {
                        "id": SYNTHETIC_FILE_ID,
                        "object": "file",
                        "bytes": len(file_bytes),
                        "created_at": 1790000000,
                        "filename": filename,
                        "purpose": purpose,
                    },
                    separators=(",", ":"),
                ).encode()
        elif method == "POST" and path.endswith("/v1/responses"):
            try:
                parsed = json.loads(request_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                status_line = b"HTTP/1.1 400 Bad Request"
                body = json.dumps(
                    {"error": {"message": "invalid JSON", "type": "invalid_request_error"}},
                    separators=(",", ":"),
                ).encode()
            else:
                ref_ids, err_msg = validate_and_extract_responses_file_ids(parsed)
                if err_msg is not None:
                    status_line = b"HTTP/1.1 400 Bad Request"
                    body = json.dumps(
                        {"error": {"message": err_msg, "type": "invalid_request_error"}},
                        separators=(",", ":"),
                    ).encode()
                else:
                    resolved_texts = []
                    missing_id = None
                    with self.server.capture_lock:
                        for fid in ref_ids:
                            record = self.server.uploaded_files.get((auth, fid))
                            if record is None:
                                missing_id = fid
                                break
                            resolved_texts.append(
                                parse_pdf_text_streams(record["bytes"])
                            )
                    if missing_id is not None:
                        status_line = b"HTTP/1.1 400 Bad Request"
                        body = json.dumps(
                            {
                                "error": {
                                    "message": f"No file found with id '{missing_id}' under this provider key.",
                                    "type": "invalid_request_error",
                                }
                            },
                            separators=(",", ":"),
                        ).encode()
                    else:
                        if resolved_texts:
                            reply_text = "Resolved uploaded PDF content: " + " | ".join(
                                resolved_texts
                            )
                        else:
                            reply_text = "NO_FILE_REFERENCED"
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
                                                "text": reply_text,
                                                "annotations": [],
                                            }
                                        ],
                                    }
                                ],
                                "usage": {
                                    "input_tokens": 12,
                                    "output_tokens": 8,
                                    "total_tokens": 20,
                                },
                            },
                            separators=(",", ":"),
                        ).encode()
        else:
            status_line = b"HTTP/1.1 404 Not Found"
            body = json.dumps(
                {"error": {"message": f"unexpected route {method} {path}"}}
            ).encode()

        response_headers = [
            status_line,
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

    def handle_live_openai(self, raw, method, path, headers, request_body):
        if path.endswith("/v1/models") or path.endswith("/models"):
            body = json.dumps(
                {"object": "list", "data": [{"id": "gpt-4o-mini", "object": "model"}]},
                separators=(",", ":"),
            ).encode()
            response = (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            self.request.sendall(response)
            return

        target_path = path if path.startswith("/v1/") else f"/v1{path}"
        url = f"https://api.openai.com{target_path}"
        forward_headers = {
            "Authorization": headers.get("authorization", ""),
        }
        if "content-type" in headers:
            forward_headers["Content-Type"] = headers["content-type"]

        req = urllib.request.Request(
            url,
            data=request_body if method in ("POST", "PUT", "PATCH") else None,
            headers=forward_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as up_resp:
                status_code = up_resp.status
                reason = up_resp.reason
                resp_body = up_resp.read()
                x_req_id = up_resp.headers.get("x-request-id", "")
                openai_model = up_resp.headers.get("openai-model", "")
        except urllib.error.HTTPError as err:
            status_code = err.code
            reason = err.reason
            resp_body = err.read()
            x_req_id = err.headers.get("x-request-id", "") if err.headers else ""
            openai_model = err.headers.get("openai-model", "") if err.headers else ""

        resp_lines = [
            f"HTTP/1.1 {status_code} {reason}".encode("latin-1"),
            b"Content-Type: application/json",
            f"Content-Length: {len(resp_body)}".encode("ascii"),
        ]
        if x_req_id:
            resp_lines.append(f"x-request-id: {x_req_id}".encode("latin-1"))
        if openai_model:
            resp_lines.append(f"openai-model: {openai_model}".encode("latin-1"))
        resp_lines.append(b"Connection: close")
        response = b"\r\n".join(resp_lines) + b"\r\n\r\n" + resp_body
        self.request.sendall(response)

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

    def __init__(self, address, live_openai=False):
        super().__init__(address, CaptureHandler)
        self.capture_lock = threading.Lock()
        self.exchanges = []
        self.uploaded_files = {}
        self.live_openai = live_openai

    def drain_exchanges(self):
        with self.capture_lock:
            items = list(self.exchanges)
            self.exchanges.clear()
            return items


def sanitize_client_bytes(raw):
    out = raw.replace(VK_TENANT_A.encode(), b"<VK_TENANT_A>")
    out = out.replace(VK_TENANT_B.encode(), b"<VK_TENANT_B>")
    return out


def sanitize_upstream_bytes(raw, provider_key):
    out = raw.replace(provider_key.encode(), b"<SHARED_PROVIDER_API_KEY>")
    return re.sub(
        rb"(?im)^(authorization:\s*bearer\s+)[^\r\n]+",
        rb"\1<SHARED_PROVIDER_API_KEY>",
        out,
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


def make_config(app_dir, upstream_port, provider_key):
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
                        "value": provider_key,
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


def wait_for_ready(port, process, log_path, provider_key):
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
        provider_key.encode(), b"<SHARED_PROVIDER_API_KEY>"
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


def delete_live_openai_file(provider_key, file_id):
    req = urllib.request.Request(
        f"https://api.openai.com/v1/files/{file_id}",
        headers={"Authorization": f"Bearer {provider_key}"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except Exception:
        pass


def run(binary, source, output_dir, live_openai=False):
    check_source(source)
    require(binary.is_file(), f"BIFROST_BIN does not exist: {binary}")
    require(os.access(binary, os.X_OK), f"BIFROST_BIN is not executable: {binary}")
    binary_provenance = check_binary(binary)

    if live_openai:
        provider_key = os.environ.get("OPENAI_API_KEY", "").strip()
        require(
            bool(provider_key),
            "OPENAI_API_KEY environment variable is required with --live-openai",
        )
    else:
        provider_key = SYNTHETIC_PROVIDER_KEY

    bifrost_port = free_port()
    upstream_port = free_port()

    capture_server = CaptureServer((HOST, upstream_port), live_openai=live_openai)
    capture_thread = threading.Thread(target=capture_server.serve_forever, daemon=True)
    capture_thread.start()
    process = None
    log_file = None
    uploaded_live_file_id = None
    try:
        with tempfile.TemporaryDirectory(prefix="kairo-148-bifrost-") as app_dir_text:
            app_dir = Path(app_dir_text)
            (app_dir / "config.json").write_text(
                json.dumps(make_config(app_dir, upstream_port, provider_key), indent=2)
                + "\n"
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
            version = wait_for_ready(bifrost_port, process, log_path, provider_key)
            capture_server.drain_exchanges()

            # Pre-upload negative check in offline mode: verify the server has 0 stored files
            # and returns 400 if an un-uploaded file_id is referenced before upload.
            if not live_openai:
                require(
                    len(capture_server.uploaded_files) == 0,
                    "stateful capture server must start with zero uploaded files",
                )

            # Step 0: Tenant A uploads a valid PDF document via POST /v1/files?provider=openai.
            pdf_bytes = make_valid_pdf(TENANT_A_PDF_CANARY)
            mp_body, mp_ct = build_multipart_file_upload(
                "tenant-a-secret.pdf", pdf_bytes, purpose="user_data"
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
            upload_json = json.loads(body.decode("utf-8"))
            tenant_a_file_id = upload_json["id"]
            if live_openai:
                uploaded_live_file_id = tenant_a_file_id
            else:
                require(
                    tenant_a_file_id == SYNTHETIC_FILE_ID,
                    f"expected {SYNTHETIC_FILE_ID}, got {tenant_a_file_id}",
                )
            upload_exchanges = capture_server.drain_exchanges()
            require(
                len(upload_exchanges) == 1,
                f"expected 1 upstream file upload, saw {len(upload_exchanges)}",
            )
            upload_transcript = sanitize_client_bytes(upload_req) + upload_resp

            prompt_text = (
                "Tell me the exact hyphenated code starting with KAIRO-148 inside the PDF."
            )

            user_msg_responses_body = json.dumps(
                {
                    "model": "openai/gpt-4o-mini",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_file", "file_id": tenant_a_file_id},
                                {"type": "input_text", "text": prompt_text},
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
                                        "file_id": tenant_a_file_id,
                                    },
                                },
                                {"type": "text", "text": prompt_text},
                            ],
                        }
                    ],
                },
                separators=(",", ":"),
            ).encode()

            no_file_ref_responses_body = json.dumps(
                {
                    "model": "openai/gpt-4o-mini",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": prompt_text,
                        },
                        {
                            "type": "function_call",
                            "call_id": "call_nofilereference_148",
                            "name": "fetch_doc",
                            "arguments": "{}",
                        },
                        {
                            "type": "function_call_output",
                            "call_id": "call_nofilereference_148",
                            "output": [
                                {
                                    "type": "input_text",
                                    "text": "No file was provided to this tool call.",
                                }
                            ],
                        },
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
                            "content": prompt_text,
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
                                {"type": "input_file", "file_id": tenant_a_file_id}
                            ],
                        },
                    ],
                },
                separators=(",", ":"),
            ).encode()

            custom_tool_output_responses_body = json.dumps(
                {
                    "model": "openai/gpt-4o-mini",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": prompt_text,
                        },
                        {
                            "type": "custom_tool_call",
                            "call_id": "call_custom_148",
                            "name": "read_doc",
                            "input": "tenant-a-secret.pdf",
                        },
                        {
                            "type": "custom_tool_call_output",
                            "call_id": "call_custom_148",
                            "output": [
                                {"type": "input_file", "file_id": tenant_a_file_id}
                            ],
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
                        {"role": "user", "content": prompt_text},
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
                                                "file_id": tenant_a_file_id,
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
                "control_no_file_reference_canary_absent_200": 0,
                "control_owner_tool_output_allowed_200": 0,
                "attack_responses_function_call_output_bypassed_200": 0,
                "attack_responses_custom_tool_call_output_bypassed_200": 0,
                "attack_anthropic_tool_result_bypassed_200": 0,
            }
            trial_rows = []

            for trial in range(1, RUNS + 1):
                # Control 1: Tenant B direct GET /v1/files/{id}?provider=openai -> 404, 0 upstream
                req, resp, status, _, body = raw_request(
                    bifrost_port,
                    "GET",
                    f"/v1/files/{tenant_a_file_id}?provider=openai",
                    virtual_key=VK_TENANT_B,
                    content_type=None,
                )
                ex = capture_server.drain_exchanges()
                require(
                    status == 404
                    and len(ex) == 0
                    and b"access_blocked" in body
                    and f"file '{tenant_a_file_id}' not found".encode() in body
                    and TENANT_A_PDF_CANARY.encode() not in body,
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
                    and f"file '{tenant_a_file_id}' not found".encode() in body
                    and TENANT_A_PDF_CANARY.encode() not in body,
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
                    and f"file '{tenant_a_file_id}' not found".encode() in body
                    and TENANT_A_PDF_CANARY.encode() not in body,
                    f"trial {trial}: expected 404 for Anthropic user message control, got {status}: {body!r}",
                )
                counts["control_anthropic_user_message_blocked_404"] += 1
                if "control-anthropic-user-message.http" not in artifacts:
                    artifacts["control-anthropic-user-message.http"] = (
                        sanitize_client_bytes(req) + resp
                    )

                # Control 4: Tenant B POST /v1/responses with function_call_output WITHOUT file_id -> 200, canary absent
                req, resp, status, _, body = raw_request(
                    bifrost_port,
                    "POST",
                    "/v1/responses",
                    body=no_file_ref_responses_body,
                    virtual_key=VK_TENANT_B,
                )
                ex = capture_server.drain_exchanges()
                require(
                    status == 200
                    and len(ex) == 1
                    and tenant_a_file_id.encode() not in ex[0]["body"]
                    and TENANT_A_PDF_CANARY.encode() not in body,
                    f"trial {trial}: expected 200 without canary on no-file-reference control, got {status}: {body!r}",
                )
                counts["control_no_file_reference_canary_absent_200"] += 1
                if "control-no-file-reference.http" not in artifacts:
                    artifacts["control-no-file-reference.http"] = (
                        sanitize_client_bytes(req) + resp
                    )

                # Control 5: Owner Tenant A POST /v1/responses with function_call_output input_file -> 200, 1 upstream, canary present
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
                    and tenant_a_file_id.encode() in ex[0]["body"]
                    and TENANT_A_PDF_CANARY.encode() in body,
                    f"trial {trial}: expected 200 with PDF canary for owner tool output control, got {status}: {body!r}",
                )
                counts["control_owner_tool_output_allowed_200"] += 1
                if "control-owner-tool-output.http" not in artifacts:
                    artifacts["control-owner-tool-output.http"] = (
                        sanitize_client_bytes(req) + resp
                    )

                # Attack 1: Tenant B POST /v1/responses with function_call_output input_file -> 200 (defect), 1 upstream, PDF canary disclosed
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
                    and tenant_a_file_id.encode() in ex[0]["body"]
                    and TENANT_A_PDF_CANARY.encode() in body,
                    f"trial {trial}: expected 200 bypass on function_call_output, got {status}: {body!r}",
                )
                counts["attack_responses_function_call_output_bypassed_200"] += 1
                if "attack-responses-function-call-output.http" not in artifacts:
                    artifacts["attack-responses-function-call-output.http"] = (
                        sanitize_client_bytes(req) + resp
                    )
                    artifacts[
                        "attack-responses-function-call-output-upstream.http"
                    ] = (
                        sanitize_upstream_bytes(ex[0]["raw_request"], provider_key)
                        + ex[0]["raw_response"]
                    )

                # Attack 2: Tenant B POST /v1/responses with custom_tool_call_output input_file -> 200 (defect), 1 upstream, PDF canary disclosed
                req, resp, status, _, body = raw_request(
                    bifrost_port,
                    "POST",
                    "/v1/responses",
                    body=custom_tool_output_responses_body,
                    virtual_key=VK_TENANT_B,
                )
                ex = capture_server.drain_exchanges()
                require(
                    status == 200
                    and len(ex) == 1
                    and tenant_a_file_id.encode() in ex[0]["body"]
                    and TENANT_A_PDF_CANARY.encode() in body,
                    f"trial {trial}: expected 200 bypass on custom_tool_call_output, got {status}: {body!r}",
                )
                counts["attack_responses_custom_tool_call_output_bypassed_200"] += 1
                if "attack-responses-custom-tool-call-output.http" not in artifacts:
                    artifacts["attack-responses-custom-tool-call-output.http"] = (
                        sanitize_client_bytes(req) + resp
                    )
                    artifacts[
                        "attack-responses-custom-tool-call-output-upstream.http"
                    ] = (
                        sanitize_upstream_bytes(ex[0]["raw_request"], provider_key)
                        + ex[0]["raw_response"]
                    )

                # Attack 3: Tenant B POST /anthropic/v1/messages with tool_result document file_id -> 200 (defect), 1 upstream, PDF canary disclosed
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
                    and tenant_a_file_id.encode() in ex[0]["body"]
                    and TENANT_A_PDF_CANARY.encode() in body,
                    f"trial {trial}: expected 200 bypass on Anthropic tool_result, got {status}: {body!r}",
                )
                counts["attack_anthropic_tool_result_bypassed_200"] += 1
                if "attack-anthropic-tool-result.http" not in artifacts:
                    artifacts["attack-anthropic-tool-result.http"] = (
                        sanitize_client_bytes(req) + resp
                    )
                    artifacts["attack-anthropic-tool-result-upstream.http"] = (
                        sanitize_upstream_bytes(ex[0]["raw_request"], provider_key)
                        + ex[0]["raw_response"]
                    )

                trial_rows.append(
                    {
                        "trial": trial,
                        "control_direct_file_retrieve_status": 404,
                        "control_responses_user_message_status": 404,
                        "control_anthropic_user_message_status": 404,
                        "control_no_file_reference_status": 200,
                        "control_no_file_reference_canary_disclosed": False,
                        "control_owner_tool_output_status": 200,
                        "control_owner_tool_output_canary_disclosed": True,
                        "attack_responses_function_call_output_status": 200,
                        "attack_responses_function_call_output_canary_disclosed": True,
                        "attack_responses_custom_tool_call_output_status": 200,
                        "attack_responses_custom_tool_call_output_canary_disclosed": True,
                        "attack_anthropic_tool_result_status": 200,
                        "attack_anthropic_tool_result_canary_disclosed": True,
                    }
                )

            results = {
                "complete": True,
                "date_checked": datetime.now().astimezone().date().isoformat(),
                "live_openai": live_openai,
                "uploaded_file_id": tenant_a_file_id,
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
                    ],
                    "provider": (
                        "openai (live https://api.openai.com via recording relay)"
                        if live_openai
                        else "openai (stateful PDF-parsing capture upstream)"
                    ),
                    "model": "openai/gpt-4o-mini",
                    "enforce_auth_on_inference": True,
                    "enforce_vk_object_ownership": True,
                },
                "runs": RUNS,
                "summary": counts,
                "trials": trial_rows,
                "secrets": (
                    "live OPENAI_API_KEY redacted to <SHARED_PROVIDER_API_KEY>; synthetic virtual keys and PDF canary"
                    if live_openai
                    else "synthetic canaries only; no real provider credential was used"
                ),
            }
            artifacts["results.json"] = results
            write_artifacts(output_dir, artifacts)
            return results
    finally:
        if uploaded_live_file_id is not None and live_openai:
            delete_live_openai_file(provider_key, uploaded_live_file_id)
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
    parser.add_argument(
        "--live-openai",
        action="store_true",
        help="forward upstream requests to live https://api.openai.com using OPENAI_API_KEY",
    )
    args = parser.parse_args()
    require(args.binary is not None, "set BIFROST_BIN or pass --binary")
    require(args.source is not None, "set BIFROST_SOURCE or pass --source")
    output_dir = args.output_dir
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="kairo-148-review-"))
    results = run(
        args.binary.resolve(),
        args.source.resolve(),
        output_dir.resolve(),
        live_openai=args.live_openai,
    )
    print(json.dumps(results, indent=2, sort_keys=True))
    print(f"artifacts: {output_dir.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.SubprocessError, ReproductionError) as exc:
        print(f"reproduction failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
