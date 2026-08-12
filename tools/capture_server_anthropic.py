import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
OUT=sys.argv[1]
CANNED={"id":"msg_cap","type":"message","role":"assistant","model":"cap",
  "content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn",
  "usage":{"input_tokens":1,"output_tokens":1}}
class H(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_POST(self):
        n=int(self.headers.get("content-length",0));b=self.rfile.read(n)
        open(OUT,"a").write(json.dumps({"path":self.path,"body":json.loads(b) if b else None})+"\n")
        r=json.dumps(CANNED).encode()
        self.send_response(200);self.send_header("content-type","application/json")
        self.send_header("content-length",str(len(r)));self.end_headers();self.wfile.write(r)
HTTPServer(("127.0.0.1",9998),H).serve_forever()
