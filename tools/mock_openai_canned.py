# Serves whatever JSON is in CANNED_FILE (re-read per request so it can be swapped live).
import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
CANNED_FILE = os.environ.get("CANNED_FILE", "/tmp/canned.json")
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        b=json.dumps({"data":[{"id":"mockmodel","object":"model"}]}).encode()
        self.send_response(200);self.send_header("content-type","application/json")
        self.send_header("content-length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_POST(self):
        n=int(self.headers.get("content-length",0)); self.rfile.read(n)
        body=open(CANNED_FILE,"rb").read()
        self.send_response(200);self.send_header("content-type","application/json")
        self.send_header("content-length",str(len(body)));self.end_headers();self.wfile.write(body)
HTTPServer(("127.0.0.1",9996),H).serve_forever()
