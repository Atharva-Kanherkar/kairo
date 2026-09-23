"""Replay the issue 084 client request directly and through LiteLLM.

    python3 transcripts/084/replay.py --output-dir /tmp/kairo-084-rerun          # reviewer run
    python3 transcripts/084/replay.py --output-dir transcripts/084/raw/replay    # maintainer fixture refresh
"""

from pathlib import Path
import argparse
import json
import socket

parser = argparse.ArgumentParser()
parser.add_argument(
    "--output-dir",
    type=Path,
    required=True,
    help="capture directory; pass the same one to capture_upstream.py",
)
OUT = parser.parse_args().output_dir.resolve()
OUT.mkdir(parents=True, exist_ok=True)
payload = {
    "id": "codex-manual-search-test",
    "model": "gpt-5.6-luna",
    "commands": {"search_query": [{"q": "OpenAI Responses API"}]},
    "settings": {"search_context_size": "low", "external_web_access": True},
    "max_output_tokens": 200,
}
body = json.dumps(payload, separators=(",", ":")).encode()
(OUT / "client-request.json").write_bytes(body)
cases = {
    "direct": (9996, "/v1/alpha/search"),
    "proxy": (4010, "/openai_passthrough/v1/alpha/search"),
}
results = {"direct": [], "proxy": []}
for case, (port, path) in cases.items():
    for i in range(1, 6):
        request = (
            f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            "Content-Type: application/json\r\nAccept: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode() + body
        (OUT / f"client-{case}-{i:02d}-request.http").write_bytes(request)
        with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
            sock.sendall(request)
            received = bytearray()
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                received.extend(chunk)
        response = bytes(received)
        (OUT / f"client-{case}-{i:02d}-response.http").write_bytes(response)
        status = int(response.split(b" ", 2)[1])
        results[case].append(status)
        print(f"{case} {i}/5 status={status}")
(OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
print(f"captures written to {OUT}")
