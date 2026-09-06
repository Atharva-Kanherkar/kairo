"""Reproduction for issue 071: LiteLLM GET /model/info and /v1/model/info leak
deployment `api_base` query credentials.

Captures literal HTTP wire bytes over a raw socket (no reconstruction from
parsed headers), records route identity and HTTP status alongside every
captured body, fails fast if the reproduction port is already occupied,
detects the spawned process exiting during startup, and only writes fixtures
after every determinism check has passed explicitly (never via a bare
`assert`, so behavior is unaffected by `python3 -O`).

    python3 transcripts/071/reproduce.py                          # reviewer run, temp dir
    python3 transcripts/071/reproduce.py --output-dir transcripts/071  # maintainer fixture refresh
"""

import argparse
import http.client
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

HOST = "127.0.0.1"
PORT = 4010
CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "litellm-leak.yaml")
)
LITELLM_BIN = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../tools/litellm-env/bin/litellm")
)
CANARY = "CANARY_QUERY_KEY_IN_API_BASE"
REQUIRED_RUNS = 5
LITELLM_VERSION = "1.99.0"
READY_TIMEOUT_SECONDS = 20

ROUTES = (
    ("/model/info", "model-info"),
    ("/v1/model/info", "model-info-v1"),
    ("/v1/models", "models-control"),
    ("/health/liveliness", "liveliness-control"),
)


class ReproductionError(Exception):
    """An actionable reproduction failure. The message must never contain a
    raw credential; only sanitized diagnostics are attached."""


_SECRET_PATTERNS = (
    re.compile(rb"([Aa]uthorization:\s*[Bb]earer\s+)\S+"),
    re.compile(rb"(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"),
    re.compile(rb"([?&][Kk]ey=)[^\s&\"'<>]+"),
)


def sanitize_diagnostic(raw: bytes) -> str:
    """Redact these known key forms in diagnostics. This is not a general
    secret detector; the fixed canary config and isolated child environment
    prevent real provider credentials from reaching this keyless reproduction."""
    text = raw
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(rb"\1[REDACTED]", text)
    return text.decode("utf-8", errors="replace")


def ensure_port_available(host: str, port: int) -> None:
    """Fail fast if something is already listening on host:port. Without this
    check, a stale process from a prior run (or an unrelated local service)
    would silently receive our probes, and the reproduction would report a
    result measured against the wrong server."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((host, port))
    except OSError as e:
        raise ReproductionError(
            f"port {port} on {host} is already in use ({e}); stop the process "
            f"holding it (for example `lsof -i :{port}`) before re-running the "
            "reproduction"
        ) from None
    finally:
        probe.close()


def start_litellm(work_dir):
    if not os.path.isfile(LITELLM_BIN):
        raise ReproductionError(
            f"LiteLLM executable not found at {LITELLM_BIN}; install "
            f"litellm[proxy]=={LITELLM_VERSION} in tools/litellm-env first"
        )
    # This keyless reproduction must not load repo .env or inherit provider keys.
    env = {
        "PATH": os.defpath,
        "HOME": work_dir,
        "LANG": "C.UTF-8",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        # dotenv can search upward from the installed module, not just cwd.
        "PYTHON_DOTENV_DISABLED": "1",
    }
    try:
        version = subprocess.run(
            [
                os.path.join(os.path.dirname(LITELLM_BIN), "python"),
                "-c",
                "from importlib.metadata import version; print(version('litellm'))",
            ],
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        raise ReproductionError("cannot check the pinned LiteLLM environment") from e
    if version != LITELLM_VERSION:
        raise ReproductionError(f"expected LiteLLM {LITELLM_VERSION}, found {version}")
    ensure_port_available(HOST, PORT)
    log_fd, log_path = tempfile.mkstemp(prefix="startup-", dir=work_dir)
    log_file = os.fdopen(log_fd, "wb")
    cmd = [LITELLM_BIN, "--config", CONFIG_PATH, "--host", HOST, "--port", str(PORT)]
    print(f"Starting LiteLLM: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=work_dir,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    except OSError as e:
        log_file.close()
        os.unlink(log_path)
        raise ReproductionError(
            f"failed to launch LiteLLM ({LITELLM_BIN}): {e}"
        ) from None
    return proc, log_file, log_path


def startup_diagnostic(log_path: str, max_bytes: int = 4096) -> str:
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            raw = f.read()
    except OSError:
        return "(no startup log captured)"
    return sanitize_diagnostic(raw) or "(startup log is empty)"


def wait_for_ready(
    proc: subprocess.Popen, log_path: str, timeout: float = READY_TIMEOUT_SECONDS
) -> None:
    """Poll readiness while also monitoring the spawned process itself. A
    process that exits during startup must be reported immediately, not
    discovered only after waiting out the full timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        exit_code = proc.poll()
        if exit_code is not None:
            raise ReproductionError(
                f"LiteLLM exited during startup (code {exit_code}) before serving "
                f"any request. Startup log (sanitized):\n{startup_diagnostic(log_path)}"
            )
        try:
            status, _, _, _ = raw_http_request("/health/liveliness")
            if status == 200 and proc.poll() is None:
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise ReproductionError(
        f"LiteLLM did not become ready on {HOST}:{PORT} within {timeout}s. "
        f"Startup log (sanitized):\n{startup_diagnostic(log_path)}"
    )


def parse_http_response(raw: bytes):
    class CapturedSocket:
        def makefile(self, mode):
            return io.BytesIO(raw)

    try:
        with http.client.HTTPResponse(CapturedSocket()) as response:
            response.begin()
            return response.status, response.read()
    except (http.client.HTTPException, ValueError) as e:
        raise ReproductionError("malformed or truncated HTTP response") from e


def raw_http_request(path: str, timeout: float = 5):
    """Send a literal GET request over a raw socket and read the literal
    response bytes off the wire. Nothing here is reconstructed from parsed
    pieces: `request_bytes` and `response_bytes` are exactly what was sent
    and received."""
    request_bytes = (
        f"GET {path} HTTP/1.1\r\nHost: {HOST}:{PORT}\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    sock = socket.create_connection((HOST, PORT), timeout=timeout)
    try:
        sock.sendall(request_bytes)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    response_bytes = b"".join(chunks)
    status, body = parse_http_response(response_bytes)
    return status, body, request_bytes, response_bytes


def make_envelope(path: str, status: int, body: bytes) -> dict:
    try:
        parsed_body = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ReproductionError(
            f"response body for {path} is not valid JSON: {e}"
        ) from None
    return {"request_path": path, "status": status, "body": parsed_body}


def envelope_contains(envelope: dict, needle: str) -> bool:
    return needle in json.dumps(envelope["body"])


def has_canary_in_api_base(envelope: dict) -> bool:
    body = envelope["body"]
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return False
    params = data[0].get("litellm_params", {})
    api_base = params.get("api_base") if isinstance(params, dict) else None
    return isinstance(api_base, str) and CANARY in api_base


def validate_results(results: dict) -> None:
    """Explicit if/raise checks, not `assert`: this validation must behave
    identically under `python3 -O`, which strips assert statements."""
    expectations = (
        ("model_info_canary_present", REQUIRED_RUNS),
        ("model_info_v1_canary_present", REQUIRED_RUNS),
        ("models_control_clean", REQUIRED_RUNS),
        ("liveliness_control_clean", REQUIRED_RUNS),
        ("runs", REQUIRED_RUNS),
    )
    failures = [
        f"{name}={results.get(name)} (expected {expected})"
        for name, expected in expectations
        if results.get(name) != expected
    ]
    if failures:
        raise ReproductionError(
            "reproduction did not reach 5/5 determinism: " + "; ".join(failures)
        )


def write_fixtures(output_dir: str, files: dict) -> None:
    """Stage contents before replacement. The caller validates probes first.
    Replacement is atomic per file, not across the batch on filesystem errors."""
    os.makedirs(output_dir, exist_ok=True)
    staging_dir = tempfile.mkdtemp(prefix=".staging-", dir=output_dir)
    try:
        staged_paths = {}
        for name, content in files.items():
            staged_path = os.path.join(staging_dir, name)
            if isinstance(content, (bytes, bytearray)):
                with open(staged_path, "wb") as f:
                    f.write(content)
            else:
                with open(staged_path, "w", encoding="utf-8") as f:
                    f.write(content)
            staged_paths[name] = staged_path
        for name, staged_path in staged_paths.items():
            os.replace(staged_path, os.path.join(output_dir, name))
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def run_probes() -> tuple:
    results = {
        "model_info_canary_present": 0,
        "model_info_v1_canary_present": 0,
        "models_control_clean": 0,
        "liveliness_control_clean": 0,
        "runs": REQUIRED_RUNS,
    }
    last: dict = {}

    for _ in range(REQUIRED_RUNS):
        for path, key in ROUTES:
            status, body, req_bytes, resp_bytes = raw_http_request(path)
            if status != 200:
                raise ReproductionError(
                    f"GET {path} returned HTTP {status}, expected 200"
                )
            envelope = make_envelope(path, status, body)
            last[f"{key}.json"] = envelope
            last[f"{key}.http"] = req_bytes + resp_bytes

        if has_canary_in_api_base(last["model-info.json"]):
            results["model_info_canary_present"] += 1
        if has_canary_in_api_base(last["model-info-v1.json"]):
            results["model_info_v1_canary_present"] += 1
        if not envelope_contains(last["models-control.json"], "CANARY"):
            results["models_control_clean"] += 1
        if not envelope_contains(last["liveliness-control.json"], "CANARY"):
            results["liveliness_control_clean"] += 1

    return results, last


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        help="capture directory; defaults to a new temporary reviewer directory",
    )
    args = parser.parse_args()
    output_dir = os.path.abspath(
        args.output_dir or tempfile.mkdtemp(prefix="kairo-071-")
    )

    with tempfile.TemporaryDirectory(prefix="kairo-071-process-") as work_dir:
        capture(output_dir, work_dir)


def capture(output_dir, work_dir):
    proc, log_file, log_path = start_litellm(work_dir)
    try:
        wait_for_ready(proc, log_path)
        print("LiteLLM is ready. Running 5/5 determinism probes...")

        results, last = run_probes()
        if proc.poll() is not None:
            raise ReproductionError(
                "LiteLLM exited during the probes; refusing to write evidence"
            )
        print(f"Results across {REQUIRED_RUNS} runs: {results}")

        # Validate BEFORE writing anything: a failed run must not touch
        # previously written evidence in output_dir.
        validate_results(results)
        results["litellm_version"] = LITELLM_VERSION
        print(
            f"All {REQUIRED_RUNS}/{REQUIRED_RUNS} probes succeeded deterministically!"
        )

        files = {"client-results.json": json.dumps(results, indent=2) + "\n"}
        for name, value in last.items():
            if name.endswith(".json"):
                files[name] = json.dumps(value, indent=2) + "\n"
            else:
                files[name] = value

        write_fixtures(output_dir, files)
        print(f"Sanitized captures written to: {output_dir}")
    finally:
        try:
            proc.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log_file.close()
        os.unlink(log_path)


if __name__ == "__main__":
    try:
        main()
    except (ReproductionError, OSError) as e:
        print(f"reproduction failed: {e}", file=sys.stderr)
        sys.exit(1)
