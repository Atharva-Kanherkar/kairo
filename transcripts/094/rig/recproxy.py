#!/usr/bin/env python3
"""Recording reverse proxy: client -> :18001 -> frontend :18000.

Saves each exchange as <dir>/<seq>-request.http and <seq>-response.http
(raw request line/headers/body; raw status/headers/body as streamed).
Authorization / x-api-key header values are replaced with [REDACTED].
"""
import http.client, http.server, itertools, os, socketserver, sys, threading

UP_HOST, UP_PORT = "localhost", 18000
OUT = os.environ.get("REC_DIR", os.path.expanduser("~/dyn-rig/wire"))
os.makedirs(OUT, exist_ok=True)
seq = itertools.count(len(os.listdir(OUT)) // 2 + 1)
lock = threading.Lock()
SECRET = {"authorization", "x-api-key"}


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _go(self):
        with lock:
            n = next(seq)
        n = f"{n:04d}"
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        with open(f"{OUT}/{n}-request.http", "wb") as f:
            f.write(f"{self.command} {self.path} HTTP/1.1\r\n".encode())
            for k, v in self.headers.items():
                f.write(f"{k}: {'[REDACTED]' if k.lower() in SECRET else v}\r\n".encode())
            f.write(b"\r\n" + body)
        conn = http.client.HTTPConnection(UP_HOST, UP_PORT, timeout=300)
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "accept-encoding")}
        conn.request(self.command, self.path, body=body, headers=hdrs)
        r = conn.getresponse()
        rf = open(f"{OUT}/{n}-response.http", "wb")
        rf.write(f"HTTP/1.1 {r.status} {r.reason}\r\n".encode())
        self.send_response(r.status, r.reason)
        for k, v in r.getheaders():
            rf.write(f"{k}: {v}\r\n".encode())
            if k.lower() in ("transfer-encoding", "content-length", "connection"):
                continue
            self.send_header(k, v)
        rf.write(b"\r\n")
        self.send_header("transfer-encoding", "chunked")
        self.send_header("connection", "close")
        self.end_headers()
        while True:
            chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
            if not chunk:
                break
            rf.write(chunk); rf.flush()
            try:
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n"); self.wfile.flush()
            except BrokenPipeError:
                break
        try:
            self.wfile.write(b"0\r\n\r\n")
        except BrokenPipeError:
            pass
        rf.close(); conn.close()
        self.close_connection = True

    do_GET = do_POST = do_DELETE = do_HEAD = _go


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    S(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 18001), H).serve_forever()
