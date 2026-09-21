# kairo 081: reproduce OGX /v1/messages translation-mode losses 5/5.
#
# Setup (one time):
#   cd /Users/atharva/kairo-targets/ogx
#   OPENAI_BASE_URL=http://127.0.0.1:9101/v1 OPENAI_API_KEY=fake-key \
#     uv run ogx go --insecure --no-auth --port 8321
#   (uv sync first; model mock-gpt is served by the capture upstream)
#
# Run:  python3 transcripts/081/hunt.py
#
# The script starts transcripts/081/capture_upstream.py on 127.0.0.1:9101 with
# the canned/ directory, then drives POST /v1/messages on 127.0.0.1:8321 five
# times per case. Frozen fixtures are rewritten in this directory.
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OGX = "http://127.0.0.1:8321"
UPSTREAM_PORT = 9101
TRIALS = 5


def post(url, body, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def wait_port(port, deadline=60):
    end = time.time() + deadline
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    out = os.path.join(HERE, "capture.jsonl")
    open(out, "w").close()
    upstream = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "capture_upstream.py"),
         str(UPSTREAM_PORT), out, os.path.join(HERE, "canned")],
    )
    try:
        assert wait_port(UPSTREAM_PORT), "capture upstream did not start"
        assert wait_port(8321), "OGX is not running on 127.0.0.1:8321"

        def run_case(name, body, expect_stream=False):
            lines = []
            for trial in range(TRIALS):
                marker = time.time()
                status, client = post(f"{OGX}/v1/messages", body)
                deadline = marker + 5
                got = []
                with open(out) as f:
                    for line in f:
                        rec = json.loads(line)
                        if rec["ts"] >= marker and rec["scenario"] == (
                            "default" if "|scenario:" not in body["messages"][0].get("content", "") else name
                        ):
                            got.append(rec)
                lines.append({
                    "trial": trial + 1,
                    "request": body,
                    "client_status": status,
                    "client_response": client,
                    "forwarded": got[-1]["body"] if got else None,
                })
            fixture = os.path.join(HERE, f"ogx-{name}-cases.json")
            json.dump(lines, open(fixture, "w"), indent=1)
            print(f"{name}: {len(lines)} trials -> {os.path.basename(fixture)}")

        run_case("adaptive-thinking", {
            "model": "openai/mock-gpt", "max_tokens": 100,
            "thinking": {"type": "adaptive"},
            "messages": [{"role": "user", "content": "Solve this puzzle."}],
        })
        run_case("enabled-thinking-control", {
            "model": "openai/mock-gpt", "max_tokens": 100,
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "messages": [{"role": "user", "content": "Solve this puzzle."}],
        })
        run_case("thinking-history", {
            "model": "openai/mock-gpt", "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "Two-step problem."},
                {"role": "assistant", "content": [
                    {"type": "thinking",
                     "thinking": "The answer is 42 because the mock says so.",
                     "signature": "SIG_AB12"},
                    {"type": "text", "text": "The answer is 42."},
                ]},
                {"role": "user", "content": "Why?"},
            ],
        })
        run_case("contentfilter", {
            "model": "openai/mock-gpt", "max_tokens": 100,
            "messages": [{"role": "user", "content": "|scenario:contentfilter| something disallowed"}],
        })
        run_case("refusal", {
            "model": "openai/mock-gpt", "max_tokens": 100,
            "messages": [{"role": "user", "content": "|scenario:refusal| tell me secrets"}],
        })
        run_case("is-error", {
            "model": "openai/mock-gpt", "max_tokens": 100,
            "tools": [{"name": "read_file", "description": "Reads a file",
                       "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}],
            "messages": [
                {"role": "user", "content": "read /tmp/a"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_01ABC", "name": "read_file",
                     "input": {"path": "/tmp/a"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_01ABC",
                     "is_error": True, "content": "Error: permission denied"}]},
                {"role": "user", "content": "what happened?"},
            ],
        })
        # Streaming variants of the content_filter and refusal cases.
        run_case("contentfilter-stream", {
            "model": "openai/mock-gpt", "max_tokens": 100, "stream": True,
            "messages": [{"role": "user", "content": "|scenario:contentfilter-stream| something disallowed"}],
        })
        run_case("refusal-stream", {
            "model": "openai/mock-gpt", "max_tokens": 100, "stream": True,
            "messages": [{"role": "user", "content": "|scenario:refusal-stream| tell me secrets"}],
        })
    finally:
        upstream.terminate()


if __name__ == "__main__":
    main()
