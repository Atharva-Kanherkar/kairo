#!/usr/bin/env python3
"""Forwarding relay used only to record what Switchyard sends a live provider.

Listens on 127.0.0.1:9997, appends the exact request line, headers and body to
a jsonl file, then forwards the request to https://api.openai.com unchanged and
pipes the response back. It adds nothing and inspects nothing: the recorded
bytes are the bytes that crossed the network.

The Authorization header value is replaced with a marker before it is written,
so the transcript cannot contain a credential.
"""
import json
import sys
import urllib.request

PORT = int(sys.argv[1])
OUT = sys.argv[2]
UPSTREAM = "https://api.openai.com"


class Handler(__import__("http.server", fromlist=["BaseHTTPRequestHandler"]).BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        headers = {k: v for k, v in self.headers.items()}
        recorded = {k: ("<credential withheld>" if k.lower() == "authorization" else v)
                    for k, v in headers.items()}
        with open(OUT, "a") as handle:
            handle.write(json.dumps({
                "upstream": UPSTREAM + self.path,
                "method": "POST",
                "headers": recorded,
                "raw": raw.decode("utf-8", "replace"),
            }) + "\n")
        outgoing = {k: v for k, v in headers.items()
                    if k.lower() not in ("host", "content-length", "connection",
                                         "accept-encoding", "content-encoding")}
        request = urllib.request.Request(UPSTREAM + self.path, data=raw, headers=outgoing)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status, body = response.status, response.read()
                content_type = response.headers.get("content-type", "application/json")
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read()
            content_type = error.headers.get("content-type", "application/json")
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    from http.server import ThreadingHTTPServer
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()