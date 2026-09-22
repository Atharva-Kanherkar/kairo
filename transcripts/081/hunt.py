"""Run the pinned OGX v1.4.0 adaptive-thinking reproduction.

The capture upstream is started first. Results go to a temporary directory by
default. Pass --refreeze only when intentionally updating the checked-in
fixtures under transcripts/081.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent
PINNED_COMMIT = "051a8a0"
TRIALS = 5


def get(url, timeout=2):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.read()


def post(url, body):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def wait_ready(url, process, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"child exited before readiness: {process.args[0]}")
        try:
            get(url)
            return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for {url}")


def git_commit(checkout):
    return subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "--short=7", "HEAD"],
        text=True,
    ).strip()


def terminate(process):
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ogx_checkout", type=Path)
    parser.add_argument("--refreeze", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--capture-port", type=int, default=9101)
    parser.add_argument("--ogx-port", type=int, default=8321)
    args = parser.parse_args()

    checkout = args.ogx_checkout.resolve()
    if not (checkout / "pyproject.toml").exists():
        raise SystemExit(f"not an OGX checkout: {checkout}")
    observed = git_commit(checkout)
    if observed != PINNED_COMMIT:
        raise SystemExit(f"expected OGX commit {PINNED_COMMIT}, found {observed}")
    if args.refreeze and args.output_dir:
        raise SystemExit("--refreeze and --output-dir cannot be combined")

    temporary = args.output_dir is None
    output = (Path(tempfile.mkdtemp(prefix="kairo-081-")) if temporary else args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    capture = output / "capture.jsonl"
    capture.write_text("", encoding="utf-8")
    upstream = None
    ogx = None
    try:
        upstream = subprocess.Popen([
            sys.executable, str(HERE / "capture_upstream.py"),
            str(args.capture_port), str(capture),
        ])
        wait_ready(f"http://127.0.0.1:{args.capture_port}/v1/models", upstream)
        environment = os.environ.copy()
        environment.update({
            "OPENAI_BASE_URL": f"http://127.0.0.1:{args.capture_port}/v1",
            "OPENAI_API_KEY": "kairo-081-synthetic-key",
        })
        ogx = subprocess.Popen([
            "uv", "run", "ogx", "go", "--insecure", "--no-auth",
            "--port", str(args.ogx_port),
        ], cwd=checkout, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_ready(f"http://127.0.0.1:{args.ogx_port}/v1/models", ogx)

        adaptive = {
            "model": "openai/mock-gpt",
            "max_tokens": 32,
            "thinking": {"type": "adaptive"},
            "messages": [{"role": "user", "content": "Synthetic adaptive-thinking control."}],
        }
        enabled = {
            **{key: value for key, value in adaptive.items() if key != "thinking"},
            "thinking": {"type": "enabled", "budget_tokens": 1024},
        }
        cases = {}
        for name, body in (("adaptive-thinking", adaptive), ("enabled-thinking-control", enabled)):
            rows = []
            for trial in range(1, TRIALS + 1):
                before = len(capture.read_text(encoding="utf-8").splitlines())
                status, response = post(f"http://127.0.0.1:{args.ogx_port}/v1/messages", body)
                deadline = time.monotonic() + 10
                forwarded = None
                while time.monotonic() < deadline:
                    records = [line for line in capture.read_text(encoding="utf-8").splitlines() if line.strip()]
                    if len(records) > before:
                        forwarded = json.loads(records[-1])["body"]
                        break
                    time.sleep(0.05)
                rows.append({
                    "trial": trial,
                    "request": body,
                    "client_status": status,
                    "client_response": response,
                    "forwarded": forwarded,
                })
            cases[name] = rows

        if args.refreeze:
            for name, rows in cases.items():
                (HERE / f"ogx-{name}-cases.json").write_text(
                    json.dumps(rows, indent=1) + "\n", encoding="utf-8"
                )
            (HERE / "capture.jsonl").write_text(capture.read_text(encoding="utf-8"), encoding="utf-8")
        adaptive_ok = sum(row["client_status"] == 200 and row["forwarded"] is not None for row in cases["adaptive-thinking"])
        enabled_ok = sum(row["client_status"] == 400 and row["forwarded"] is None for row in cases["enabled-thinking-control"])
        print(f"OGX {observed}: adaptive {adaptive_ok}/{TRIALS} HTTP 200 with capture; enabled control {enabled_ok}/{TRIALS} HTTP 400")
        print(f"sanitized output: {output}")
    finally:
        if ogx is not None:
            terminate(ogx)
        if upstream is not None:
            terminate(upstream)


if __name__ == "__main__":
    main()
