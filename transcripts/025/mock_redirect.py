# 302 the request to another local capture so we can see if auth follows.
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
OUT = sys.argv[2]
LOCATION = sys.argv[3]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _record_and_redirect(self):
        n = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(n) if n else b""
        rec = {
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body_len": len(raw),
        }
        with open(OUT, "a") as f:
            f.write(json.dumps(rec) + "\n")
        self.send_response(302)
        self.send_header("Location", LOCATION)
        self.end_headers()

    def do_POST(self):
        self._record_and_redirect()

    def do_GET(self):
        self._record_and_redirect()


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
