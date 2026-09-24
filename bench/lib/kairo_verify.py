"""Helpers shared by every task's reproducer and hidden verifier.

Stdlib only, Python 3.9+. Copied into each container at /opt/kairo-lib and put
on PYTHONPATH by the harness.

- ``Results``: named checks written to ``$KAIRO_RESULTS`` as the grading contract.
- ``Upstream``: a deterministic local HTTP upstream that records every request
  and answers from a caller-supplied function (JSON, raw bytes, or SSE).
- ``Service``: start the system under test, wait for it, and stop it.
- ``request`` and ``sse_events``: minimal HTTP client and SSE parser.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable, Optional, Union

# ------------------------------------------------------------------ results


class Results:
    """Collect named test outcomes. Unknown exceptions become ``error``."""

    def __init__(self) -> None:
        self.tests: list[dict] = []

    def record(self, name: str, status: str, detail: str = "") -> None:
        self.tests.append({"name": name, "status": status, "detail": detail[:4000]})
        mark = {"pass": "PASS", "fail": "FAIL"}.get(status, "ERROR")
        print(f"[{mark}] {name}" + (f": {detail[-1500:]}" if detail and status != "pass" else ""), flush=True)

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        self.record(name, "pass" if condition else "fail", "" if condition else detail)
        return bool(condition)

    @contextlib.contextmanager
    def test(self, name: str):
        """``with results.test("name"):`` passes unless an assertion fails."""
        try:
            yield
        except AssertionError as exc:
            self.record(name, "fail", str(exc) or "assertion failed")
        except Exception:
            self.record(name, "error", traceback.format_exc()[-3000:])
        else:
            self.record(name, "pass")

    def fail_all(self, names: Iterable[str], detail: str) -> None:
        """Mark tests that could not run (for example the service never started)."""
        done = {t["name"] for t in self.tests}
        for name in names:
            if name not in done:
                self.record(name, "fail", detail)

    def write(self, path: Optional[str] = None) -> None:
        path = path or os.environ.get("KAIRO_RESULTS") or "results.json"
        with open(path, "w") as fh:
            json.dump({"tests": self.tests}, fh, indent=2)
        passed = sum(t["status"] == "pass" for t in self.tests)
        print(f"{passed}/{len(self.tests)} checks passed -> {path}", flush=True)


# ------------------------------------------------------------------ http client


def request(method: str, url: str, body: Any = None, headers: Optional[dict] = None,
            timeout: float = 60.0) -> tuple[int, dict, bytes]:
    """Send one request. ``body`` may be a dict (sent as JSON), str, or bytes."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.hostname, parts.port, timeout=timeout)
    hdrs = dict(headers or {})
    data: Optional[bytes] = None
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif isinstance(body, str):
        data = body.encode()
    elif isinstance(body, bytes):
        data = body
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, raw
    finally:
        conn.close()


def post_json(url: str, body: Any, headers: Optional[dict] = None, timeout: float = 60.0) -> tuple[int, Any, bytes]:
    status, _, raw = request("POST", url, body, headers, timeout)
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None
    return status, parsed, raw


def sse_events(raw: Union[bytes, str]) -> list[dict]:
    """Parse an SSE body into ``[{"event": str|None, "data": str, "json": obj|None}]``."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        event, data_lines = None, []
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:][1:] if line[5:].startswith(" ") else line[5:])
        if not data_lines and event is None:
            continue
        data = "\n".join(data_lines)
        try:
            parsed = json.loads(data) if data and data != "[DONE]" else None
        except json.JSONDecodeError:
            parsed = None
        events.append({"event": event, "data": data, "json": parsed})
    return events


# ------------------------------------------------------------------ upstream


class Captured:
    """One request received by :class:`Upstream`."""

    def __init__(self, method: str, path: str, headers: dict, body: bytes) -> None:
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body

    @property
    def json(self) -> Any:
        try:
            return json.loads(self.body) if self.body else None
        except json.JSONDecodeError:
            return None

    def to_dict(self) -> dict:
        return {"method": self.method, "path": self.path, "headers": self.headers,
                "body": self.json if self.json is not None else self.body.decode("utf-8", "replace")}


class Reply:
    """What the upstream sends back. Use the constructors below."""

    def __init__(self, status: int = 200, headers: Optional[dict] = None, body: bytes = b"",
                 chunks: Optional[Iterable[bytes]] = None, delay: float = 0.0,
                 declared_length: Optional[int] = None) -> None:
        """``declared_length`` larger than ``body`` simulates a transport drop: the
        upstream announces that many bytes, sends ``body``, and closes the socket."""
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.chunks = chunks
        self.delay = delay
        self.declared_length = declared_length

    @classmethod
    def json(cls, obj: Any, status: int = 200, headers: Optional[dict] = None) -> "Reply":
        return cls(status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(obj).encode())

    @classmethod
    def sse(cls, events: Iterable[Union[dict, str, tuple]], status: int = 200, headers: Optional[dict] = None,
            done: bool = False, delay: float = 0.0) -> "Reply":
        """``events`` items: a dict (``data:`` JSON), a raw string, or ``(event_name, dict)``."""
        chunks = []
        for ev in events:
            if isinstance(ev, tuple):
                name, payload = ev
                chunks.append(f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode())
            elif isinstance(ev, dict):
                chunks.append(f"data: {json.dumps(ev)}\n\n".encode())
            else:
                chunks.append(ev.encode() if isinstance(ev, str) else ev)
        if done:
            chunks.append(b"data: [DONE]\n\n")
        return cls(status, {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", **(headers or {})},
                   chunks=chunks, delay=delay)


class Upstream:
    """Deterministic local upstream. ``responder(captured) -> Reply``."""

    def __init__(self, responder: Callable[[Captured], Reply], port: int = 0, host: str = "127.0.0.1") -> None:
        self.responder = responder
        self.requests: list[Captured] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                    body = self._read_chunked()
                else:
                    body = self.rfile.read(length) if length else b""
                cap = Captured(self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                with outer._lock:
                    outer.requests.append(cap)
                try:
                    reply = outer.responder(cap)
                except Exception:
                    traceback.print_exc()
                    reply = Reply.json({"error": {"message": "kairo upstream responder crashed"}}, 500)
                self.send_response(reply.status)
                for k, v in reply.headers.items():
                    self.send_header(k, v)
                if reply.chunks is not None:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    try:
                        for chunk in reply.chunks:
                            if reply.delay:
                                time.sleep(reply.delay)
                            self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                            self.wfile.flush()
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    self.close_connection = True
                else:
                    self.send_header("Content-Length", str(reply.declared_length or len(reply.body)))
                    self.end_headers()
                    self.wfile.write(reply.body)
                    if reply.declared_length and reply.declared_length > len(reply.body):
                        # Let the client consume what was sent, then drop the socket short of
                        # the declared length.
                        self.wfile.flush()
                        time.sleep(0.3)
                        self.close_connection = True
                        with contextlib.suppress(OSError):
                            self.connection.shutdown(socket.SHUT_RDWR)

            def _read_chunked(self) -> bytes:
                out = b""
                while True:
                    size = int(self.rfile.readline().strip() or b"0", 16)
                    if size == 0:
                        self.rfile.readline()
                        return out
                    out += self.rfile.read(size)
                    self.rfile.readline()

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://{host}:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "Upstream":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()

    def last(self, path_contains: str = "") -> Optional[Captured]:
        with self._lock:
            for cap in reversed(self.requests):
                if path_contains in cap.path:
                    return cap
        return None

    def dump(self, path: str) -> None:
        with open(path, "w") as fh:
            for cap in self.requests:
                fh.write(json.dumps(cap.to_dict()) + "\n")


# ------------------------------------------------------------------ services


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, host: str = "127.0.0.1", timeout: float = 120.0, proc: Optional[subprocess.Popen] = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.25)
    return False


def wait_http(url: str, timeout: float = 120.0, proc: Optional[subprocess.Popen] = None,
              ok: Callable[[int], bool] = lambda s: s < 500) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            status, _, _ = request("GET", url, timeout=2)
            if ok(status):
                return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


class Service:
    """Run the system under test as a subprocess in its own process group."""

    def __init__(self, argv: list[str], *, env: Optional[dict] = None, cwd: Optional[str] = None,
                 log_path: str = "service.log") -> None:
        self.argv = argv
        self.env = {**os.environ, **(env or {})}
        self.cwd = cwd
        self.log_path = log_path
        self.proc: Optional[subprocess.Popen] = None

    def __enter__(self) -> "Service":
        self._log = open(self.log_path, "ab")
        self.proc = subprocess.Popen(self.argv, env=self.env, cwd=self.cwd, stdout=self._log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.proc and self.proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
        self._log.close()

    def log_tail(self, n: int = 40) -> str:
        try:
            with open(self.log_path, "rb") as fh:
                return b"\n".join(fh.read().splitlines()[-n:]).decode("utf-8", "replace")
        except OSError:
            return ""


def run(cmd: Union[str, list[str]], *, cwd: Optional[str] = None, timeout: float = 1800,
        env: Optional[dict] = None, log_path: Optional[str] = None) -> tuple[int, str]:
    """Run a build or test command; return (exit code, combined output tail)."""
    shell = isinstance(cmd, str)
    proc = subprocess.run(cmd, shell=shell, cwd=cwd, env={**os.environ, **(env or {})},
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    out = proc.stdout.decode("utf-8", "replace")
    if log_path:
        with open(log_path, "a") as fh:
            fh.write(out)
    return proc.returncode, out[-6000:]


def show(title: str, obj: Any) -> None:
    """Pretty-print evidence for a human (used by reproducers)."""
    print(f"\n==== {title} ====")
    if isinstance(obj, (bytes, bytearray)):
        obj = obj.decode("utf-8", "replace")
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except (json.JSONDecodeError, ValueError):
            print(obj)
            return
    print(json.dumps(obj, indent=2, sort_keys=False))
    sys.stdout.flush()
