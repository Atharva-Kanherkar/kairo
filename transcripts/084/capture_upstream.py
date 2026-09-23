"""Deterministic standalone-search upstream for issue 084.

Answers HTTP 200 when the JSON body carries a top-level `id` and the
provider-style missing-parameter 400 otherwise, recording every exchange.

    python3 transcripts/084/capture_upstream.py --output-dir /tmp/kairo-084-rerun          # reviewer run
    python3 transcripts/084/capture_upstream.py --output-dir transcripts/084/raw/replay    # maintainer fixture refresh
"""

from pathlib import Path
import argparse
import json
import socketserver
import threading

parser = argparse.ArgumentParser()
parser.add_argument(
    "--output-dir",
    type=Path,
    required=True,
    help="capture directory; pass the same one to replay.py",
)
OUT = parser.parse_args().output_dir.resolve()
OUT.mkdir(parents=True, exist_ok=True)
print(f"capturing to {OUT}", flush=True)
count = 0
lock = threading.Lock()

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        global count
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = self.request.recv(65536)
            if not chunk:
                return
            data.extend(chunk)
        split = data.index(b"\r\n\r\n") + 4
        head = bytes(data[:split])
        headers = head[:-4].split(b"\r\n")[1:]
        length = 0
        chunked = False
        for line in headers:
            key, _, value = line.partition(b":")
            if key.lower() == b"content-length":
                length = int(value.strip())
            if key.lower() == b"transfer-encoding" and b"chunked" in value.lower():
                chunked = True
        while len(data) < split + length or (chunked and not data.endswith(b"0\r\n\r\n")):
            chunk = self.request.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
        raw = bytes(data)
        with lock:
            count += 1
            index = count
        # The proxy's synthetic upstream credential headers are irrelevant and are
        # redacted inline before the capture is persisted.
        lines = raw.split(b"\r\n")
        sensitive = (b"authorization:", b"api-key:", b"x-api-key:", b"x-goog-api-key:", b"openai-api-key:")
        lines = [line.split(b":", 1)[0] + b": [REDACTED]" if line.lower().startswith(sensitive) else line for line in lines]
        sanitized = b"\r\n".join(lines)
        (OUT / f"upstream-request-{index:02d}.http").write_bytes(sanitized)
        body = raw[split:split + length]
        if chunked:
            pieces = []
            rest = raw[split:]
            while rest:
                size_line, _, rest = rest.partition(b"\r\n")
                try:
                    size = int(size_line.split(b";", 1)[0], 16)
                except ValueError:
                    break
                if size == 0:
                    break
                pieces.append(rest[:size])
                rest = rest[size + 2:]
            body = b"".join(pieces)
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}
        if "id" not in payload:
            status = 400
            reason = b"Bad Request"
            response = b'{"error":{"message":"Missing required parameter: id","type":"invalid_request_error","param":"id","code":"missing_required_parameter"}}'
        else:
            status = 200
            reason = b"OK"
            response = json.dumps({"id": payload["id"], "object": "search.results", "results": [{"title": "synthetic control"}]}).encode()
        raw_response = (f"HTTP/1.1 {status} ".encode() + reason + b"\r\nContent-Type: application/json\r\nContent-Length: " + str(len(response)).encode() + b"\r\nConnection: close\r\n\r\n" + response)
        (OUT / f"upstream-response-{index:02d}.http").write_bytes(raw_response)
        self.request.sendall(raw_response)

class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

Server(("127.0.0.1", 9996), Handler).serve_forever()
