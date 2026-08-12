import json
from http.server import BaseHTTPRequestHandler, HTTPServer
BODY=json.dumps({"id":"chatcmpl-x","object":"chat.completion","created":0,"model":"m","choices":[],"usage":{"prompt_tokens":1,"completion_tokens":0,"total_tokens":1}}).encode()
class H(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_POST(self):
        n=int(self.headers.get("content-length",0));self.rfile.read(n)
        self.send_response(200);self.send_header("content-type","application/json")
        self.send_header("content-length",str(len(BODY)));self.end_headers();self.wfile.write(BODY)
    def do_GET(self):
        b=json.dumps({"data":[{"id":"mockmodel","object":"model"}]}).encode()
        self.send_response(200);self.send_header("content-type","application/json")
        self.send_header("content-length",str(len(b)));self.end_headers();self.wfile.write(b)
HTTPServer(("127.0.0.1",9997),H).serve_forever()
