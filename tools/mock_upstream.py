# Mock upstream: records every request body, replies with canned content.
# If CANNED file ends in .sse -> streams it as SSE; else JSON body.
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT, OUT, CANNED = int(sys.argv[1]), sys.argv[2], sys.argv[3]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = self.rfile.read(n)
        with open(OUT, "a") as f:
            f.write(json.dumps({"path": self.path,
                "body": json.loads(body) if body else None}) + "\n")
        if CANNED.endswith(".sse"):
            data = open(CANNED, "rb").read()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(data)
        else:
            resp = open(CANNED, "rb").read()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

HTTPServer(("127.0.0.1", PORT), H).serve_forever()
